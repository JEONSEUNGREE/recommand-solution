"""광고주 상품(rv_products + product_enriched + product_descriptions) 검색 API.

- POST /search-rv          : 광고주별 멀티모달 벡터 검색 (bge or openai)
- POST /search-rv-llm      : LLM(Groq)이 자연어를 정제된 쿼리+필터로 변환 후 검색
- GET  /products-rv/{id}   : 상품 상세 (벡터 점수 + 이미지 메타 + enriched 전체)
- POST /embed/openai       : OpenAI 임베딩 위임 (backend 대신 사이드카가 호출)
"""
from __future__ import annotations

import json
import os
import time
from typing import Literal, Optional

import psycopg
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

DB_DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")
OPENAI_MODEL = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
# 격리된 sparse-embedder(:8002) — 하이브리드 검색 시 쿼리 sparse 인코딩 위임.
# (이 프로세스(:8001)는 FlagEmbedding 비호환 → sparse 는 별도 서비스가 담당)
SPARSE_BASE = os.environ.get("SPARSE_EMBEDDER_BASE", "http://localhost:8002")
# BGE-M3 의 XLM-R 토크나이저가 한국어를 음절 단위로 쪼개 1자 토큰(예: "골","귀")이
# sparse 점수에 노이즈로 들어감 → 쿼리측 sparse 에서 디코딩 길이 미만 토큰 제외.
# 내적은 곱셈이라 한쪽만 0이면 기여 0 → 쿼리만 필터해도 동일 효과(DB 재백필 불필요).
# 0 또는 1 = 필터 없음. env 로 조정 가능. 기본 2 (단음절 노이즈 컷).
SPARSE_MIN_TOKEN_CHARS = int(os.environ.get("SPARSE_MIN_TOKEN_CHARS", "2"))
# ColBERT reranker(:8003) — dense top-K를 ColBERT MaxSim으로 재정렬.
# 미설정 또는 서비스 미기동 시 dense 순서 유지(graceful fallback).
COLBERT_BASE = os.environ.get("COLBERT_BASE", "http://localhost:8003")

router = APIRouter()


def _log_search(
    *,
    query: str,
    semantic_query: Optional[str],
    llm_used: bool,
    llm_provider: Optional[str],
    backend: str,
    advertiser_id: Optional[int],
    result_count: int,
    keyword_filters: Optional[list],
    keyword_filter_relaxed: bool,
    items: Optional[list] = None,
    llm_duration_ms: Optional[int] = None,
    embed_duration_ms: Optional[int] = None,
    total_duration_ms: Optional[int] = None,
    llm_input_tokens: Optional[int] = None,
    llm_output_tokens: Optional[int] = None,
    llm_cost_usd: Optional[float] = None,
    llm_parsed_json: Optional[dict] = None,
) -> None:
    try:
        conn = psycopg.connect(DB_DSN)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO search_logs
                   (query, semantic_query, llm_used, llm_provider, backend,
                    advertiser_id, result_count, keyword_filters, keyword_filter_relaxed,
                    llm_duration_ms, embed_duration_ms, total_duration_ms,
                    llm_input_tokens, llm_output_tokens, llm_cost_usd, llm_parsed_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::text[], %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                   RETURNING id""",
                (
                    query, semantic_query, llm_used, llm_provider, backend,
                    advertiser_id, result_count,
                    keyword_filters or [],
                    keyword_filter_relaxed,
                    llm_duration_ms, embed_duration_ms, total_duration_ms,
                    llm_input_tokens, llm_output_tokens, llm_cost_usd,
                    json.dumps(llm_parsed_json, ensure_ascii=False) if llm_parsed_json else None,
                ),
            )
            log_id = cur.fetchone()[0]

            if items:
                for rank, item in enumerate(items, start=1):
                    tags = item.get("tags")
                    cur.execute(
                        """INSERT INTO search_log_results
                           (log_id, rank, rv_product_id, product_name, product_code,
                            image_url, product_url, price, sale_price,
                            category, brand, distance, perspective, description,
                            tags, desc_persona,
                            dense_dist, sparse_ip, morph_ip, fusion_score,
                            rank_dense, rank_sparse, rank_morph)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                                   %s,%s,%s,%s,%s,%s,%s)""",
                        (
                            log_id, rank,
                            item.get("rv_product_id"), item.get("product_name"), item.get("product_code"),
                            item.get("image_url"), item.get("product_url"),
                            item.get("price"), item.get("sale_price"),
                            item.get("category"), item.get("brand"),
                            float(item.get("distance", 0)),
                            item.get("perspective"), item.get("description"),
                            json.dumps(tags, ensure_ascii=False) if tags is not None else None,
                            item.get("desc_persona"),
                            # 하이브리드 신호별 점수 (dense 모드면 전부 None)
                            item.get("dense_dist"), item.get("sparse_ip"), item.get("morph_ip"),
                            item.get("score"),
                            item.get("rank_dense"), item.get("rank_sparse"), item.get("rank_morph"),
                        ),
                    )
        conn.commit()
        conn.close()
    except Exception as e:
        import sys
        print(f"[search_log ERROR] {e}", file=sys.stderr)


# bge-m3 인코딩은 공유 로더(bge_model)를 통한다. 싱글톤이라 프로세스당 1회만 로드.
def _embed_bge(text: str) -> list[float]:
    from . import bge_model
    return bge_model.encode_dense([text])[0]


def _embed_openai(text: str) -> list[float]:
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set on embedder process")
    from openai import OpenAI
    client = OpenAI()
    r = client.embeddings.create(model=OPENAI_MODEL, input=text)
    return r.data[0].embedding


def _embed_sparse(text: str) -> Optional[str]:
    """격리된 sparse-embedder(:8002)에 쿼리 텍스트를 보내 pgvector sparsevec 리터럴 획득.

    SPARSE_MIN_TOKEN_CHARS 가 1보다 크면 단음절 토큰을 쿼리측에서 제외.
    서비스 미기동/오류 시 None 반환 → 호출부가 dense-only 로 폴백한다.
    """
    try:
        r = requests.post(
            f"{SPARSE_BASE}/embed-sparse",
            json={"texts": [text], "min_token_chars": SPARSE_MIN_TOKEN_CHARS},
            timeout=20,
        )
        r.raise_for_status()
        arr = r.json().get("sparse") or []
        return arr[0] if arr else None
    except Exception as e:
        import sys
        print(f"[search_rv] sparse embed 실패 (dense 폴백): {e}", file=sys.stderr)
        return None


def _embed_morpheme(text: str) -> Optional[str]:
    """:8002 /embed-morpheme — 한국어 형태소(Kiwi+TF-IDF) sparsevec 리터럴 획득.

    BGE-M3 가 음절로 쪼개 못 잡는 단어 단위 매칭용. 미기동/오류 시 None.
    """
    try:
        r = requests.post(
            f"{SPARSE_BASE}/embed-morpheme",
            json={"texts": [text]},
            timeout=20,
        )
        r.raise_for_status()
        arr = r.json().get("sparse") or []
        return arr[0] if arr else None
    except Exception as e:
        import sys
        print(f"[search_rv] morpheme embed 실패 (해당 신호 생략): {e}", file=sys.stderr)
        return None


def _embed_colbert(text: str) -> list[list[float]] | None:
    """colbert-reranker(:8003)에 쿼리 텍스트를 보내 ColBERT 토큰 벡터 획득.

    서비스 미기동/오류 시 None → 호출부가 dense 순서 유지(graceful fallback).
    """
    try:
        r = requests.post(
            f"{COLBERT_BASE}/embed-colbert",
            json={"text": text},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("colbert_vecs")
    except Exception as e:
        import sys
        print(f"[search_rv] colbert embed 실패 (dense 순서 유지): {e}", file=sys.stderr)
        return None


def _colbert_rerank(
    query_colbert: list[list[float]],
    items: list[dict],
    conn,
) -> list[dict]:
    """dense 결과를 ColBERT MaxSim으로 재정렬.

    product_descriptions.colbert_vecs(jsonb)를 top-K 후보에 대해서만 조회해
    MaxSim 점수를 계산한다. colbert_vecs가 없는 행은 dense 점수(-distance)로 대체.
    """
    import numpy as np

    if not query_colbert or not items:
        return items

    pairs_pid = [it.get("rv_product_id") for it in items]
    pairs_psp = [it.get("perspective") for it in items]

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rv_product_id, perspective, colbert_vecs
                  FROM product_descriptions
                 WHERE (rv_product_id, perspective) IN (
                       SELECT unnest(%s::int[]), unnest(%s::text[]))
                   AND colbert_vecs IS NOT NULL
                """,
                (pairs_pid, pairs_psp),
            )
            vecs_map = {
                (r["rv_product_id"], r["perspective"]): r["colbert_vecs"]
                for r in cur.fetchall()
            }
    except Exception as e:
        import sys
        print(f"[colbert_rerank] colbert_vecs 조회 실패: {e}", file=sys.stderr)
        return items

    q = np.array(query_colbert, dtype=np.float32)
    scored = []
    for it in items:
        d_vecs = vecs_map.get((it.get("rv_product_id"), it.get("perspective")))
        if d_vecs:
            d = np.array(d_vecs, dtype=np.float32)
            sims = q @ d.T           # Q×K
            score = float(sims.max(axis=1).sum())
        else:
            # ColBERT 벡터 없으면 dense 거리를 부호 반전(거리 작을수록 유사)
            score = -float(it.get("distance", 1.0))
        it["colbert_score"] = round(score, 5)
        scored.append((score, it))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored]


def _project_for_viz(qvec: list[float], item_vecs: list[list[float]],
                     distances: list[float]) -> tuple[list[float], list[list[float]]]:
    """질문 중심 임베딩 유사도 맵 좌표를 만든다.

    naïve PCA([query]+items)는 질문 벡터가 결과들과 가장 다른 단일점이라
    제1주성분이 '질문 vs 결과' 축이 돼버린다 → 질문이 한쪽 끝으로 튀고 상품들은
    반대편에 뭉쳐 "가까울수록 유사"라는 의미가 깨진다.

    대신:
      - **질문 = 원점(0,0,0)** 으로 고정.
      - **반경(radius) = 코사인거리** — 질문에 가까울수록(=유사할수록) 중심에 가깝게.
      - **방향(angle) = 상품들끼리의 PCA** — 서로 비슷한 상품은 비슷한 방향으로 모임.

    반환: (query_coord3d, [item_coord3d...]) — 입력 순서 유지. 좌표는 ~[-1,1] 범위.
    """
    import numpy as np

    n = len(item_vecs)
    if n == 0:
        return [0.0, 0.0, 0.0], []

    X = np.asarray(item_vecs, dtype=np.float64)
    # 코사인 기하 → 단위 정규화
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)

    # 방향: 상품 단위벡터를 평균 중심화한 뒤 상위 3 주성분에 투영 (상품 간 상대 배치)
    if n >= 2:
        centered = X - X.mean(axis=0)
        vt = np.linalg.svd(centered, full_matrices=False)[2]
        k = min(3, vt.shape[0])
        ang = centered @ vt[:k].T
        if k < 3:
            ang = np.hstack([ang, np.zeros((n, 3 - k))])
    else:
        ang = np.array([[1.0, 0.0, 0.0]])

    # 각 상품의 방향을 단위벡터로 (방향만 취하고 크기는 반경이 결정)
    dnorm = np.linalg.norm(ang, axis=1, keepdims=True)
    dnorm[dnorm == 0] = 1.0
    dirs = ang / dnorm

    # 반경: 코사인거리를 [0.4, 1.0]로 스케일 — 0건 방지·시각적 분리 확보
    dist = np.asarray(distances, dtype=np.float64)
    dmin, dmax = float(dist.min()), float(dist.max())
    if dmax > dmin:
        rad = 0.40 + 0.60 * (dist - dmin) / (dmax - dmin)
    else:
        rad = np.full(n, 0.7)

    coords = dirs * rad[:, None]
    return [0.0, 0.0, 0.0], coords.tolist()


# ─────────────────────────────────────────────────────────────
# OpenAI 임베딩 위임 (backend가 호출 가능하게)
# ─────────────────────────────────────────────────────────────
class EmbedOpenAIReq(BaseModel):
    texts: list[str]


@router.post("/embed/openai")
def embed_openai_route(req: EmbedOpenAIReq):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set")
    from openai import OpenAI
    client = OpenAI()
    r = client.embeddings.create(model=OPENAI_MODEL, input=req.texts)
    return {
        "model": OPENAI_MODEL,
        "dim": len(r.data[0].embedding),
        "vectors": [d.embedding for d in r.data],
    }


# ─────────────────────────────────────────────────────────────
# 검색 — 광고주 선택 + 임베딩 모델 선택
# ─────────────────────────────────────────────────────────────
class SearchRvReq(BaseModel):
    query: str
    advertiser_id: Optional[int] = None      # null = 전체 광고주
    backend: Literal["bge", "openai"] = "bge"
    perspective: Optional[str] = None         # situation/material/style/persona 또는 null=any
    k: int = 10


@router.post("/search-rv")
def search_rv(req: SearchRvReq):
    t0 = time.time()
    t_embed = time.time()
    if req.backend == "openai":
        qvec = _embed_openai(req.query)
        col = "embedding_openai"
        dim = 1536
    else:
        qvec = _embed_bge(req.query)
        col = "embedding_bge"
        dim = 1024
    embed_duration_ms = int((time.time() - t_embed) * 1000)

    where = [f"pd.{col} IS NOT NULL"]
    params: list = []
    if req.advertiser_id is not None:
        where.append("pd.advertiser_id = %s")
        params.append(req.advertiser_id)
    if req.perspective:
        where.append("pd.perspective = %s")
        params.append(req.perspective)

    # 4관점 전부 거리 계산 → 상품별 평균 거리로 랭킹.
    # perspective/description 은 가장 가까운 관점(표시용)을 유지.
    sql = f"""
        WITH all_dist AS (
            SELECT pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
                   pd.{col} <=> %s::vector AS distance
              FROM product_descriptions pd
             WHERE {' AND '.join(where)}
        ),
        ranked AS (
            SELECT rv_product_id, advertiser_id,
                   AVG(distance) AS distance,
                   (array_agg(perspective ORDER BY distance))[1] AS perspective,
                   (array_agg(description ORDER BY distance))[1] AS description
              FROM all_dist
             GROUP BY rv_product_id, advertiser_id
        )
        SELECT r.rv_product_id, r.advertiser_id, r.perspective, r.description, r.distance,
               rv.product_code, rv.product_name, rv.product_url, rv.image_url,
               rv.price, rv.sale_price,
               pe.category, pe.brand, pe.brand_tier, pe.target_gender,
               pe.target_age_min, pe.target_age_max, pe.tags, pe.desc_persona
          FROM ranked r
          JOIN rv_products rv ON rv.id = r.rv_product_id
          LEFT JOIN product_enriched pe ON pe.rv_product_id = r.rv_product_id
         ORDER BY r.distance ASC
         LIMIT %s
    """
    final_params = [qvec] + params + [req.k]

    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, final_params)
            rows = cur.fetchall()
            cols = ["rv_product_id","advertiser_id","perspective","description","distance",
                    "product_code","product_name","product_url","image_url",
                    "price","sale_price",
                    "category","brand","brand_tier","target_gender","target_age_min","target_age_max",
                    "tags","desc_persona"]
        items = []
        for r in rows:
            d = dict(zip(cols, r))
            d["distance"] = float(d["distance"])
            items.append(d)
        # 단순 검색용 debug_sql
        vec_literal = "[" + ",".join(f"{v:.6f}" for v in qvec) + "]"
        where_clause = "\n       AND ".join(where[1:]) if len(where) > 1 else "(없음)"
        debug_sql = f"""WITH best AS (
    SELECT DISTINCT ON (pd.rv_product_id)
           pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
           pd.{col} <=> '{vec_literal}'::vector AS distance
      FROM product_descriptions pd
      LEFT JOIN product_enriched pe ON pe.rv_product_id = pd.rv_product_id
     WHERE pd.{col} IS NOT NULL
       AND {where_clause}
     ORDER BY pd.rv_product_id, pd.{col} <=> '{vec_literal}'::vector
)
SELECT b.rv_product_id, b.perspective, b.description, b.distance,
       rv.product_name, rv.product_code, pe.category
  FROM best b
  JOIN rv_products rv ON rv.id = b.rv_product_id
  LEFT JOIN product_enriched pe ON pe.rv_product_id = b.rv_product_id
 ORDER BY b.distance ASC
 LIMIT {req.k};"""
        _log_search(
            query=req.query, semantic_query=None, llm_used=False, llm_provider=None,
            backend=req.backend, advertiser_id=req.advertiser_id,
            result_count=len(items), keyword_filters=None, keyword_filter_relaxed=False,
            items=items, embed_duration_ms=embed_duration_ms,
            total_duration_ms=int((time.time() - t0) * 1000),
        )
        return {"backend": req.backend, "dim": dim, "advertiser_id": req.advertiser_id,
                "k": req.k, "debug_sql": debug_sql, "items": items}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# LLM 단계 (Groq Llama) — 자연어 → 정제 쿼리 + 정형 필터 → 임베딩 검색
# ─────────────────────────────────────────────────────────────
GROQ_BASE = os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# 키워드 하드필터는 ILIKE 부분문자열 매칭이라, 흔한 단어의 부분문자열이 되는
# 짧은 단어는 필터를 무력화한다. 예: "스타" ⊂ "스타일" → 거의 모든 상품 매칭.
# LLM 이 실수로 넣어도 서버에서 제거한다.
_KEYWORD_BLOCKLIST = {"스타"}

_LLM_PARSE_PROMPT = """너는 한국 쇼핑몰 검색어 분석가다. 사용자의 자연어 쿼리를 적극적으로 분석해 다음 JSON으로 변환하라.

## 스키마
{
  "semantic_query": "임베딩 검색용 표현 — filters로 분리되는 성별/연령/브랜드/가격만 빼고, 상황·소재·디자인·착용맥락·사회적신호(예: 연예인 착용·인기·셀럽·방송 착용)는 절대 버리지 말고 원문 의도 그대로 유지",
  "filters": {
    "target_gender": "여성|남성|공용" 또는 null,
    "target_age_min": int 또는 null,
    "target_age_max": int 또는 null,
    "category_contains": "카테고리 필터 키워드 (예: '귀걸이', '바람막이', '세럼') 또는 null",
    "perspective_preference": "situation|material|style|persona" 또는 null,
    "keyword_filters": ["결과를 반드시 한정해야 하는 명시적 속성 키워드 — 동의어 펼침"] (없으면 []),
    "price_min": int(원) 또는 null,
    "price_max": int(원) 또는 null,
    "price_tier": "budget|mid|premium|luxury" 또는 null
  },
  "notes": "결정 사유 한 줄"
}

## 추론 가이드 — 적극 활용
- 성별 (target_gender): 상품을 사용·착용할 **구매자** 성별을 의미한다. 연예인/모델이 착용했다는 문맥에서의 성별은 target_gender가 아니다.
  · "여자/여성/엄마/딸을 위한 상품" → 여성
  · "남자/남성/아빠/아들을 위한 상품" → 남성
  · "남자연예인 착용", "여자연예인 착용" → target_gender=null (연예인 성별 ≠ 구매자 성별)
  · 명시 없으면 null
- 연예인 착용 + 성별: "남자연예인 착용" → target_gender=null, keyword_filters에 "남성 연예인" 추가
  · "여자연예인 착용" → target_gender=null, keyword_filters에 "여성 연예인" 추가
- 연령: "20대" → min=20, max=29 / "30~40대" → min=30, max=49 / "어린이/키즈" → max=12 / 명시 없으면 null
- 가격 표현 처리:
  · "N만원대" / "N만원 내외" → price_min=N*10000*0.9, price_max=N*10000*1.1 (예: "5만원대" → min=45000, max=55000)
  · "N만원 이하" / "N만원 미만" → price_max=N*10000
  · "N만원 이상" / "N만원 넘는" → price_min=N*10000
  · "N~M만원" / "N만원~M만원" → price_min=N*10000, price_max=M*10000
  · "저렴한" / "싼" / "절약" → price_tier="budget" (price_min/max=null)
  · "가성비" / "합리적인 가격" → price_tier="mid"
  · "비싼" / "고급" / "럭셔리" / "고가" → price_tier="premium"
  · "프리미엄" / "명품급" / "하이엔드" → price_tier="luxury"
  · 가격 표현이 없으면 price_min=null, price_max=null, price_tier=null
  · price_min/max는 항상 원(KRW) 단위 정수로
- perspective_preference 결정:
  - 상황/계절/TPO 강한 쿼리 ("봄 데이트", "여행용", "출근복") → situation
  - 소재/원단/성분 강한 쿼리 ("실크 100%", "14k 골드", "히알루론산") → material
  - 디자인/색감/실루엣 ("미니멀 블랙", "크롭 핏", "로맨틱 핑크") → style
  - 타겟 페르소나 강조 ("워킹맘에게", "30대 직장인용") → persona
- 가격 표현은 notes에 기록 (price 컬럼 분리 아직 안 됨)
- keyword_filters 결정 — 결과를 "반드시" 그 속성으로 한정해야 하는 명시적·사실적 키워드만:
  · 특정 연예인/인플루언서 이름이 명시된 경우 → 그 이름을 반드시 포함시켜라
    예: "손예진 귀걸이" → ["손예진"] / "트와이스 나연 착용" → ["트와이스","나연"]
  · "연예인/셀럽/스타가 착용" 처럼 이름 없이 카테고리어만 있는 경우 → ["연예인","셀럽","셀러브리티","아이돌"]
    ※ "스타"는 "스타일"의 부분문자열이라 절대 넣지 마라
  · 구체적 형태/모양 한정어 ("하트모양", "나비모양", "꽃모양", "별모양", "링", "후프") → 핵심 단어를 keyword_filters에 추가
    예: "하트모양 귀걸이" → ["하트"] / "나비 귀걸이" → ["나비"] / "후프 귀걸이" → ["후프"]
  · 구체적 소재 한정어 ("14k", "18k", "실버", "골드", "다이아몬드", "진주") → keyword_filters에 추가
    예: "14k 귀걸이" → ["14k"] / "진주 목걸이" → ["진주"]
  · 봄·데이트·미니멀·우아한 등 분위기·상황·감성어는 절대 넣지 마라 (그건 semantic_query 로만)
  · 명시적 한정어가 없으면 [] (빈 배열)

## 예시

쿼리: "봄에 데이트하기 좋은 여성 14k 귀걸이 5만원대"
→ {"semantic_query":"봄 데이트 14k 귀걸이","filters":{"brand_tier":null,"target_gender":"여성","target_age_min":null,"target_age_max":null,"category_contains":"귀걸이","perspective_preference":"situation","keyword_filters":[]},"notes":"가격 5만원대"}

쿼리: "손예진 귀걸이"
→ {"semantic_query":"손예진 귀걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"귀걸이","perspective_preference":null,"keyword_filters":["손예진"]},"notes":"특정 연예인 이름 직접 명시 → 이름 그대로 keyword_filters에"}

쿼리: "트와이스 나연 착용 목걸이"
→ {"semantic_query":"트와이스 나연 목걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"목걸이","perspective_preference":null,"keyword_filters":["트와이스","나연"]},"notes":"그룹+멤버 모두 keyword_filters에"}

쿼리: "연예인 착용 14k 로즈골드 귀걸이"
→ {"semantic_query":"연예인 착용 로즈골드 귀걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"귀걸이","perspective_preference":null,"keyword_filters":["연예인","셀럽","셀러브리티","아이돌"]},"notes":"이름 없이 카테고리어만 → 동의어 펼침"}

쿼리: "30대 워킹맘이 출근할 때 입을 미니멀 블랙 바람막이"
→ {"semantic_query":"미니멀 블랙 바람막이","filters":{"target_gender":"여성","target_age_min":30,"target_age_max":39,"category_contains":"바람막이","perspective_preference":"persona","keyword_filters":[]},"notes":"워킹맘 페르소나 강조"}

쿼리: "히알루론산 함유 럭셔리 세럼"
→ {"semantic_query":"히알루론산 럭셔리 세럼","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"세럼","perspective_preference":"material","keyword_filters":[]},"notes":"가격 표현은 semantic_query에 포함, 필터 없음"}

쿼리: "남자연예인이 착용한 귀걸이"
→ {"semantic_query":"남자연예인 착용 귀걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"귀걸이","perspective_preference":null,"keyword_filters":["남성 연예인"]},"notes":"연예인 성별은 구매자 성별 아님 → target_gender=null, 남성 연예인 키워드 필터"}

쿼리: "여자연예인이 착용한 목걸이"
→ {"semantic_query":"여자연예인 착용 목걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"목걸이","perspective_preference":null,"keyword_filters":["여성 연예인"]},"notes":"연예인 성별은 구매자 성별 아님 → target_gender=null, 여성 연예인 키워드 필터"}

쿼리: "귀걸이"
→ {"semantic_query":"귀걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"귀걸이","perspective_preference":null,"keyword_filters":[]},"notes":"단순 키워드"}

쿼리: "비싸보이지만 저렴한 귀걸이"
→ {"semantic_query":"고급스러운 느낌의 저렴한 귀걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"귀걸이","perspective_preference":"style","keyword_filters":[]},"notes":"가격 표현은 semantic_query에 포함"}

쿼리: "럭셔리해 보이는 가성비 목걸이"
→ {"semantic_query":"고급스러운 느낌의 가성비 목걸이","filters":{"target_gender":null,"target_age_min":null,"target_age_max":null,"category_contains":"목걸이","perspective_preference":"style","keyword_filters":[]},"notes":"가격 표현은 semantic_query에 포함"}

## 출력 규칙
- JSON 한 덩어리만. 코드펜스 금지. 한국어로.
- 정보 추출이 애매하면 null. 너무 보수적이지 마라 — 명시되어 있으면 반드시 추출.
- semantic_query는 filters로 빠진 정형값(성별/연령/브랜드/가격)만 제거. 그 외 단어는 의미가 약해 보여도 절대 임의로 빼지 마라.
"""


def _llm_parse_query(query: str, provider: str = "groq") -> dict:
    """provider별 LLM 호출 → 자연어 정제 쿼리+필터. 실패 시 raw query 그대로.

    provider: 'groq' (Llama-3.3-70b) / 'openai' (gpt-4o-mini)
    반환값에 '_usage' 키로 토큰 사용량 포함: {input_tokens, output_tokens, cost_usd}
    """
    fallback = {"semantic_query": query, "filters": {}, "notes": f"{provider} 폴백", "_usage": {}}
    try:
        if provider == "openai":
            if not os.environ.get("OPENAI_API_KEY"):
                return {**fallback, "notes": "OPENAI_API_KEY 미설정"}
            from openai import OpenAI
            client = OpenAI()
            r = client.chat.completions.create(
                model=OPENAI_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": _LLM_PARSE_PROMPT},
                    {"role": "user", "content": query},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                max_tokens=500,
            )
            content = r.choices[0].message.content
            usage = r.usage
            in_tok = usage.prompt_tokens if usage else 0
            out_tok = usage.completion_tokens if usage else 0
            # gpt-4o-mini: $0.15/MTok input, $0.60/MTok output
            cost = in_tok * 0.00000015 + out_tok * 0.0000006
        else:  # groq
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                return {**fallback, "notes": "GROQ_API_KEY 미설정"}
            resp = requests.post(
                f"{GROQ_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": _LLM_PARSE_PROMPT},
                        {"role": "user", "content": query},
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "max_tokens": 500,
                },
                timeout=15,
            )
            resp.raise_for_status()
            resp_json = resp.json()
            content = resp_json["choices"][0]["message"]["content"]
            usage = resp_json.get("usage", {})
            in_tok = usage.get("prompt_tokens", 0)
            out_tok = usage.get("completion_tokens", 0)
            # groq llama-3.3-70b: $0.59/MTok input, $0.79/MTok output (무료 티어 사용 중)
            cost = in_tok * 0.00000059 + out_tok * 0.00000079
        data = json.loads(content)
        data.setdefault("semantic_query", query)
        data.setdefault("filters", {})
        data["provider"] = provider
        data["_usage"] = {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": round(cost, 8)}
        return data
    except Exception as e:
        return {**fallback, "notes": f"{provider} 실패: {e}"}


class SparseExplainReq(BaseModel):
    query: str
    doc: str
    top_k: int = 30


@router.post("/sparse-explain")
def sparse_explain_proxy(req: SparseExplainReq):
    """격리된 sparse-embedder(:8002) 의 단어 분해 결과를 프록시.

    검색 점수와 일관되게 단음절 토큰을 제외(SPARSE_MIN_TOKEN_CHARS).
    UI 가 단일 베이스(:8001)만 알면 되도록 우리가 한 번 더 감싼다.
    """
    try:
        r = requests.post(
            f"{SPARSE_BASE}/sparse-explain",
            json={"query": req.query, "doc": req.doc, "top_k": req.top_k,
                  "min_token_chars": SPARSE_MIN_TOKEN_CHARS},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(503, f"sparse-embedder 응답 실패: {e}")


@router.post("/morpheme-explain")
def morpheme_explain_proxy(req: SparseExplainReq):
    """:8002 /morpheme-explain 프록시 — 한국어 형태소 단어 매칭 분해."""
    try:
        r = requests.post(
            f"{SPARSE_BASE}/morpheme-explain",
            json={"query": req.query, "doc": req.doc, "top_k": req.top_k},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(503, f"morpheme-explain 응답 실패: {e}")


class SearchRvLlmReq(BaseModel):
    query: str
    advertiser_id: Optional[int] = None
    backend: Literal["bge", "openai"] = "bge"
    llm_provider: Literal["groq", "openai"] = "groq"
    k: int = 10
    # 하이브리드 검색. hybrid 는 backend="bge" 일 때만 동작.
    # 신호: dense(의미) + sparse(BGE 어휘) + morpheme(한국어 형태소, 선택).
    mode: Literal["dense", "hybrid"] = "dense"
    fusion: Literal["rrf", "alpha"] = "rrf"
    # dense:sparse 비중 (morpheme 끌 때). dense=alpha, sparse=1-alpha.
    alpha: float = 0.5
    # 형태소 신호 포함 여부 + 명시 가중치(셋 다 주면 정규화해서 사용, 아니면 alpha 로 유도)
    use_morpheme: bool = False
    w_dense: Optional[float] = None
    w_sparse: Optional[float] = None
    w_morpheme: Optional[float] = None


@router.post("/search-rv-llm")
def search_rv_llm(req: SearchRvLlmReq):
    """LLM이 쿼리를 정제·필터화한 다음 벡터 검색."""
    t0 = time.time()
    t_llm = time.time()
    parsed = _llm_parse_query(req.query, req.llm_provider)
    llm_duration_ms = int((time.time() - t_llm) * 1000)
    semantic_query = parsed.get("semantic_query") or req.query
    filters = parsed.get("filters") or {}

    t_embed = time.time()
    if req.backend == "openai":
        qvec = _embed_openai(semantic_query)
        col = "embedding_openai"
        dim = 1536
    else:
        qvec = _embed_bge(semantic_query)
        col = "embedding_bge"
        dim = 1024

    # 하이브리드: backend=bge 일 때만. sparse/morpheme 쿼리 인코딩을 :8002 에 위임.
    # 서비스 미기동이면 해당 신호를 빼고 가능한 신호로 폴백(끝까지 없으면 dense-only).
    hybrid = req.mode == "hybrid" and req.backend == "bge"
    hybrid_note = None
    qsparse = None
    qmorph = None
    use_morph = bool(req.use_morpheme)
    if req.mode == "hybrid" and req.backend != "bge":
        hybrid = False
        hybrid_note = "hybrid 는 backend=bge 에서만 지원 — dense 로 진행"
    elif hybrid:
        qsparse = _embed_sparse(semantic_query)
        if use_morph:
            qmorph = _embed_morpheme(semantic_query)
            if qmorph is None:
                use_morph = False
                hybrid_note = "형태소 서비스 응답 없음 — sparse 만 사용"
        if qsparse is None and qmorph is None:
            hybrid = False
            hybrid_note = "sparse-embedder(:8002) 응답 없음 — dense 로 폴백"

    # 가중치 결정: (w_dense, w_sparse, w_morpheme), 합=1
    def _resolve_weights() -> tuple[float, float, float]:
        if not hybrid:
            return (1.0, 0.0, 0.0)
        wd, ws, wm = req.w_dense, req.w_sparse, req.w_morpheme
        if wd is not None and ws is not None and (not use_morph or wm is not None):
            wm = wm if (use_morph and wm is not None) else 0.0
        else:
            # alpha 로 유도: dense:sparse = alpha:(1-alpha), morpheme 켜면 1/3 배정
            wm = (1.0 / 3.0) if use_morph else 0.0
            wd = req.alpha * (1 - wm)
            ws = (1 - req.alpha) * (1 - wm)
        if qsparse is None:
            ws = 0.0
        if not use_morph or qmorph is None:
            wm = 0.0
        tot = wd + ws + wm
        if tot <= 0:
            return (1.0, 0.0, 0.0)
        return (wd / tot, ws / tot, wm / tot)

    w_dense, w_sparse, w_morph = _resolve_weights()
    embed_duration_ms = int((time.time() - t_embed) * 1000)

    where = [f"pd.{col} IS NOT NULL"]
    params: list = []
    if req.advertiser_id is not None:
        where.append("pd.advertiser_id = %s")
        params.append(req.advertiser_id)

    # 가격 하드필터 — rv_products JOIN (2단계 완화 대상 아님)
    rv_price_join = ""
    if filters.get("price_min") is not None or filters.get("price_max") is not None:
        rv_price_join = "JOIN rv_products rv_p ON rv_p.id = pd.rv_product_id"
        if filters.get("price_min") is not None:
            where.append("COALESCE(rv_p.sale_price, rv_p.price) >= %s")
            params.append(int(filters["price_min"]))
        if filters.get("price_max") is not None:
            where.append("COALESCE(rv_p.sale_price, rv_p.price) <= %s")
            params.append(int(filters["price_max"]))

    # 카테고리는 쿼리의 핵심 — 가격과 동일하게 하드필터로 스냅샷 전에 추가.
    # 2단계 완화(pe_filter_relaxed)가 발동해도 카테고리는 절대 제거되지 않는다.
    if filters.get("category_contains"):
        where.append("pe.category ILIKE %s")
        params.append(f"%{filters['category_contains']}%")

    # endorser(연예인) 태그 중 verified=false 인 상품 제외 — 잘못된 endorser 정보가
    # 검색에 노출되지 않게 항상 적용. /products 검수에서 verified=true 로 바뀐 상품은
    # 자동으로 다시 검색에 나옴. 완화 단계에서도 절대 풀지 않음(base_where 스냅샷 이전).
    # pe 는 LEFT JOIN 이라 enriched 가 없는 행은 pe.tags IS NULL → NOT EXISTS=TRUE 로 통과.
    where.append(
        "NOT EXISTS (SELECT 1 FROM jsonb_array_elements("
        "CASE WHEN jsonb_typeof(pe.tags)='array' THEN pe.tags ELSE '[]'::jsonb END"
        ") t WHERE t->>'tag_category'='endorser' "
        "AND COALESCE(t->>'verified','false') <> 'true')"
    )

    # pe 정형 필터 추가 전 스냅샷 — 2단계 완화(gender/age 제거)에 사용
    base_where = where.copy()
    base_params = params.copy()

    # 완화 가능한 정형 필터 (gender / age)
    pe_where = []
    if filters.get("target_gender"):
        pe_where.append("pe.target_gender = %s")
        params.append(filters["target_gender"])
    if filters.get("target_age_min") is not None:
        pe_where.append("pe.target_age_max >= %s")
        params.append(int(filters["target_age_min"]))
    if filters.get("target_age_max") is not None:
        pe_where.append("pe.target_age_min <= %s")
        params.append(int(filters["target_age_max"]))

    # rv_price_join 을 pe LEFT JOIN 앞에 배치 — INNER JOIN이 LEFT JOIN 안에 묻히는
    # PostgreSQL 파싱 우선순위 버그(LEFT JOIN (pe JOIN rv_p ...))를 방지한다.
    pe_join = "LEFT JOIN product_enriched pe ON pe.rv_product_id = pd.rv_product_id"
    if pe_where:
        where.append("(" + " AND ".join(pe_where) + ")")

    # 키워드 하드필터 — LLM이 뽑은 명시적 속성어(연예인·셀럽 등)를 OR 그룹으로.
    # description / tags 만 검색 (raw_response 는 거대 JSON 이라 성능 위해 제외).
    # 부분문자열 충돌 단어(_KEYWORD_BLOCKLIST)는 제거.
    kw_raw = filters.get("keyword_filters") or []
    keywords = [k.strip() for k in kw_raw
                if isinstance(k, str) and k.strip() and k.strip() not in _KEYWORD_BLOCKLIST]
    kw_clause = None
    kw_params: list = []
    if keywords:
        ors = []
        for kw in keywords:
            ors.append("(pd.description ILIKE %s OR pe.tags::text ILIKE %s)")
            kw_params += [f"%{kw}%", f"%{kw}%"]
        kw_clause = "(" + " OR ".join(ors) + ")"

    def _build_debug_sql(where_list: list, where_params: list, vec: list) -> str:
        """복붙 가능한 SQL 생성 — %s를 실제 값으로 치환."""
        vec_literal = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
        sql_tpl = f"""WITH best AS (
    SELECT DISTINCT ON (pd.rv_product_id)
           pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
           pd.{col} <=> '{vec_literal}'::vector AS distance
      FROM product_descriptions pd
      {rv_price_join}
      {pe_join}
     WHERE {chr(10) + '       AND '.join(where_list)}
     ORDER BY pd.rv_product_id, pd.{col} <=> '{vec_literal}'::vector
)
SELECT b.rv_product_id, b.advertiser_id, b.perspective, b.description,
       b.distance,
       rv.product_code, rv.product_name, rv.product_url, rv.image_url,
       pe.category, pe.brand, pe.brand_tier, pe.target_gender,
       pe.target_age_min, pe.target_age_max
  FROM best b
  JOIN rv_products rv ON rv.id = b.rv_product_id
  LEFT JOIN product_enriched pe ON pe.rv_product_id = b.rv_product_id
 ORDER BY b.distance ASC
 LIMIT {req.k};"""
        # %s 치환 (벡터 제외한 나머지 파라미터)
        result = sql_tpl
        for p in where_params:
            val = f"'{p}'" if isinstance(p, str) else str(p)
            result = result.replace("%s", val, 1)
        return result

    cols = ["rv_product_id", "advertiser_id", "perspective", "description", "distance",
            "product_code", "product_name", "product_url", "image_url",
            "price", "sale_price",
            "category", "brand", "brand_tier", "target_gender", "target_age_min", "target_age_max",
            "tags", "desc_persona", "emb"]

    # 4관점 전부 검색 → DISTINCT ON 으로 상품별 최근접 관점 1행만 (중복 상품 제거).
    # dedup 을 top-K 자르기보다 먼저 해야 결과가 k개 미만으로 줄지 않는다.
    def _run(conn, where_list: list, where_params: list) -> list:
        sql = f"""
            WITH all_dist AS (
                SELECT pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
                       pd.{col} <=> %s::vector AS distance,
                       pd.{col}::text AS emb
                  FROM product_descriptions pd
                  {rv_price_join}
                  {pe_join}
                 WHERE {' AND '.join(where_list)}
            ),
            ranked AS (
                SELECT rv_product_id, advertiser_id,
                       MIN(distance) AS distance,
                       (array_agg(perspective ORDER BY distance))[1] AS perspective,
                       (array_agg(description ORDER BY distance))[1] AS description,
                       (array_agg(emb ORDER BY distance))[1] AS emb
                  FROM all_dist
                 GROUP BY rv_product_id, advertiser_id
            )
            SELECT r.rv_product_id, r.advertiser_id, r.perspective, r.description, r.distance,
                   rv.product_code, rv.product_name, rv.product_url, rv.image_url,
                   rv.price, rv.sale_price,
                   pe.category, pe.brand, pe.brand_tier, pe.target_gender,
                   pe.target_age_min, pe.target_age_max, pe.tags, pe.desc_persona,
                   r.emb
              FROM ranked r
              JOIN rv_products rv ON rv.id = r.rv_product_id
              LEFT JOIN product_enriched pe ON pe.rv_product_id = r.rv_product_id
             ORDER BY r.distance ASC
             LIMIT %s
        """
        with conn.cursor() as cur:
            cur.execute(sql, [qvec] + where_params + [req.k])
            rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["distance"] = float(d["distance"])
            out.append(d)
        return out

    # 형태소 신호 사용 여부 (쿼리 인코딩 성공 시에만)
    use_m = use_morph and qmorph is not None
    use_s = qsparse is not None

    cols_h = ["rv_product_id", "advertiser_id", "perspective", "description",
              "dense_dist", "sparse_ip", "morph_ip",
              "product_code", "product_name", "product_url", "image_url",
              "price", "sale_price",
              "category", "brand", "brand_tier", "target_gender",
              "target_age_min", "target_age_max", "tags", "desc_persona", "emb"]

    def _fuse(cands: list[dict]) -> list[dict]:
        """dense + sparse(+morpheme) 결합. 거리/내적은 모두 '작을수록 좋음'.

        fusion="rrf"  : 각 신호 순위로 w/(60+rank) 가중합 (스케일 무관, robust).
        fusion="alpha": 후보셋 내 min-max 정규화 후 가중합.
        가중치 = (w_dense, w_sparse, w_morph) — _resolve_weights() 에서 정규화됨.
        """
        if not cands:
            return []
        wd, ws, wm = w_dense, w_sparse, w_morph

        if req.fusion == "rrf":
            K = 60
            for i, c in enumerate(sorted(cands, key=lambda x: x["dense_dist"]), 1):
                c["rank_dense"] = i
            if use_s:
                for i, c in enumerate(sorted(cands, key=lambda x: x["sparse_ip"]), 1):
                    c["rank_sparse"] = i
            if use_m:
                for i, c in enumerate(sorted(cands, key=lambda x: x["morph_ip"]), 1):
                    c["rank_morph"] = i
            for c in cands:
                s = wd / (K + c["rank_dense"])
                if use_s:
                    s += ws / (K + c["rank_sparse"])
                if use_m:
                    s += wm / (K + c["rank_morph"])
                c["score"] = s
        else:  # alpha — min-max 정규화 (각 신호 1=best)
            def _norm_key(key):
                vals = [c[key] for c in cands]
                lo, hi = min(vals), max(vals)
                return lo, hi
            dlo, dhi = _norm_key("dense_dist")
            if use_s:
                slo, shi = _norm_key("sparse_ip")
            if use_m:
                mlo, mhi = _norm_key("morph_ip")
            for c in cands:
                dn = (dhi - c["dense_dist"]) / (dhi - dlo) if dhi > dlo else 1.0
                c["dense_norm"] = dn
                s = wd * dn
                if use_s:
                    sn = (shi - c["sparse_ip"]) / (shi - slo) if shi > slo else 1.0
                    c["sparse_norm"] = sn
                    s += ws * sn
                if use_m:
                    mn = (mhi - c["morph_ip"]) / (mhi - mlo) if mhi > mlo else 1.0
                    c["morph_norm"] = mn
                    s += wm * mn
                c["score"] = s
        cands.sort(key=lambda x: x["score"], reverse=True)
        return cands[:req.k]

    def _run_hybrid(conn, where_list: list, where_params: list) -> list:
        # 필터에 맞는 상품 전체의 dense(+sparse+morpheme) 거리를 모아 Python 에서 융합.
        # (광고주당 ~1천 상품 규모 → 전량 페치 OK. 10만+ 시 후보 캡 도입 필요.)
        # 신호별 SQL 조각 — 사용하는 신호만 SELECT/파라미터에 포함.
        sel_sparse = "pd.embedding_sparse <#> %s::sparsevec AS sparse_ip," if use_s else "0.0 AS sparse_ip,"
        sel_morph  = "pd.embedding_morpheme <#> %s::sparsevec AS morph_ip," if use_m else "0.0 AS morph_ip,"
        # 파라미터 순서: dense, [sparse], [morpheme], then where_params
        vec_params: list = [qvec]
        if use_s:
            vec_params.append(qsparse)
        if use_m:
            vec_params.append(qmorph)

        sql = f"""
            WITH all_dist AS (
                SELECT pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
                       pd.embedding_bge <=> %s::vector AS dense_dist,
                       {sel_sparse}
                       {sel_morph}
                       pd.embedding_bge::text AS emb
                  FROM product_descriptions pd
                  {rv_price_join}
                  {pe_join}
                 WHERE {' AND '.join(where_list)}
            ),
            ranked AS (
                SELECT rv_product_id, advertiser_id,
                       MIN(dense_dist) AS dense_dist,
                       MIN(sparse_ip)  AS sparse_ip,
                       MIN(morph_ip)   AS morph_ip,
                       (array_agg(perspective ORDER BY dense_dist))[1] AS perspective,
                       (array_agg(description ORDER BY dense_dist))[1] AS description,
                       (array_agg(emb ORDER BY dense_dist))[1] AS emb
                  FROM all_dist
                 GROUP BY rv_product_id, advertiser_id
            )
            SELECT r.rv_product_id, r.advertiser_id, r.perspective, r.description,
                   r.dense_dist, r.sparse_ip, r.morph_ip,
                   rv.product_code, rv.product_name, rv.product_url, rv.image_url,
                   rv.price, rv.sale_price,
                   pe.category, pe.brand, pe.brand_tier, pe.target_gender,
                   pe.target_age_min, pe.target_age_max, pe.tags, pe.desc_persona,
                   r.emb
              FROM ranked r
              JOIN rv_products rv ON rv.id = r.rv_product_id
              LEFT JOIN product_enriched pe ON pe.rv_product_id = r.rv_product_id
        """
        with conn.cursor() as cur:
            cur.execute(sql, vec_params + where_params)
            rows = cur.fetchall()
        cands = []
        for r in rows:
            d = dict(zip(cols_h, r))
            d["dense_dist"] = float(d["dense_dist"])
            d["sparse_ip"] = float(d["sparse_ip"])
            d["morph_ip"] = float(d["morph_ip"])
            cands.append(d)
        fused = _fuse(cands)
        for d in fused:
            d["distance"] = d["dense_dist"]  # 기존 viz/유사도 표시 호환(=dense 코사인거리)
        return fused

    run = _run_hybrid if hybrid else _run

    conn = psycopg.connect(DB_DSN)
    try:
        keyword_relaxed = False
        pe_filter_relaxed = False
        active_where = where
        active_params = params
        if kw_clause:
            items = run(conn, where + [kw_clause], params + kw_params)
            if not items:
                # 1단계 완화: 키워드 필터 제거
                items = run(conn, where, params)
                keyword_relaxed = True
            else:
                active_where = where + [kw_clause]
                active_params = params + kw_params
        else:
            items = run(conn, where, params)

        # 2단계 완화: 여전히 0건이면 pe 정형 필터(brand_tier/gender/age/category)도 제거
        if not items and pe_where:
            items = run(conn, base_where, base_params)
            pe_filter_relaxed = True
            active_where = base_where
            active_params = base_params

        # 벡터 공간 시각화용 3D PCA — 쿼리 + 결과 상품 임베딩을 함께 투영.
        # 무거운 원본 임베딩(emb)은 좌표 계산에만 쓰고 응답/로그에서 제거한다.
        query_coord3d = None
        if items:
            try:
                embs = [json.loads(it["emb"]) for it in items]
                dists = [it["distance"] for it in items]
                query_coord3d, item_coords = _project_for_viz(qvec, embs, dists)
                for it, c in zip(items, item_coords):
                    it["coord3d"] = c
            except Exception as e:
                import sys
                print(f"[pca ERROR] {e}", file=sys.stderr)
            finally:
                for it in items:
                    it.pop("emb", None)

        # ColBERT 리랭킹 — dense(+hybrid) top-K를 MaxSim으로 재정렬.
        # colbert-reranker(:8003) 미기동 시 graceful fallback(dense 순서 유지).
        if items:
            q_colbert = _embed_colbert(semantic_query or req.query)
            if q_colbert:
                items = _colbert_rerank(q_colbert, items, conn)

        debug_sql = _build_debug_sql(active_where, active_params, qvec)
        _usage = parsed.get("_usage") or {}
        total_ms = int((time.time() - t0) * 1000)
        _parsed_for_log = {k: v for k, v in parsed.items() if k != "_usage"}
        # search_logs.backend 에 모드/융합/가중치 표기 (별도 컬럼 없이 가시화)
        if hybrid:
            sig = "ds" + ("m" if use_m else "")  # dense+sparse(+morph)
            backend_label = (f"{req.backend}+{sig}:{req.fusion}"
                             f"(d={w_dense:.2g},s={w_sparse:.2g}"
                             f"{f',m={w_morph:.2g}' if use_m else ''})")
        else:
            backend_label = req.backend
        _log_search(
            query=req.query, semantic_query=semantic_query, llm_used=True,
            llm_provider=req.llm_provider, backend=backend_label,
            advertiser_id=req.advertiser_id, result_count=len(items),
            keyword_filters=keywords, keyword_filter_relaxed=keyword_relaxed or pe_filter_relaxed,
            items=items, llm_duration_ms=llm_duration_ms, embed_duration_ms=embed_duration_ms,
            total_duration_ms=total_ms,
            llm_input_tokens=_usage.get("input_tokens"),
            llm_output_tokens=_usage.get("output_tokens"),
            llm_cost_usd=_usage.get("cost_usd"),
            llm_parsed_json=_parsed_for_log,
        )
        return {
            "backend": req.backend,
            "mode": "hybrid" if hybrid else "dense",
            "fusion": req.fusion if hybrid else None,
            "alpha": req.alpha if hybrid else None,
            "use_morpheme": use_m,
            "weights": ({"dense": round(w_dense, 3), "sparse": round(w_sparse, 3),
                         "morpheme": round(w_morph, 3)} if hybrid else None),
            "hybrid_note": hybrid_note,
            "dim": dim,
            "advertiser_id": req.advertiser_id,
            "k": req.k,
            "raw_query": req.query,
            "parsed": parsed,
            "keyword_filters_applied": keywords,
            "keyword_filter_relaxed": keyword_relaxed,
            "pe_filter_relaxed": pe_filter_relaxed,
            "debug_sql": debug_sql,
            "items": items,
            "query_coord3d": query_coord3d,
            "llm_duration_ms": llm_duration_ms,
            "embed_duration_ms": embed_duration_ms,
            "total_duration_ms": total_ms,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 상품 상세 — enriched 전체 + 4관점 distance + 이미지 리스트
# ─────────────────────────────────────────────────────────────
@router.get("/products-rv/{rv_product_id}/detail")
def product_detail(rv_product_id: int):
    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT pe.rv_product_id, pe.advertiser_id, pe.product_code,
                          pe.category, pe.category_confidence,
                          pe.brand, pe.brand_tier, pe.price_tier,
                          pe.target_gender, pe.target_age_min, pe.target_age_max,
                          pe.origin_country, pe.manufacturer,
                          pe.category_attributes, pe.compatible_products, pe.set_components,
                          pe.tags,
                          pe.desc_situation, pe.desc_material, pe.desc_style, pe.desc_persona,
                          pe.image_types,
                          pe.model_used, pe.input_tokens, pe.output_tokens,
                          pe.cache_read_input_tokens, pe.cache_creation_input_tokens,
                          pe.cost_usd, pe.duration_ms, pe.enriched_at,
                          rv.product_code, rv.product_name, rv.product_url, rv.image_url,
                          rv.image_dir
                     FROM product_enriched pe
                     JOIN rv_products rv ON rv.id = pe.rv_product_id
                    WHERE pe.rv_product_id = %s""",
                (rv_product_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "not found")
            cols = ["rv_product_id","advertiser_id","product_code","category","category_confidence",
                    "brand","brand_tier","price_tier","target_gender","target_age_min","target_age_max",
                    "origin_country","manufacturer","category_attributes","compatible_products","set_components",
                    "tags","desc_situation","desc_material","desc_style","desc_persona","image_types",
                    "model_used","input_tokens","output_tokens","cache_read_input_tokens","cache_creation_input_tokens",
                    "cost_usd","duration_ms","enriched_at",
                    "rv_product_code","product_name","product_url","image_url","image_dir"]
            data = dict(zip(cols, row))

            # 4관점 description 메타 (벡터 채워졌는지)
            cur.execute(
                """SELECT perspective,
                          length(description) AS desc_len,
                          (embedding_bge IS NOT NULL) AS has_bge,
                          (embedding_openai IS NOT NULL) AS has_openai
                     FROM product_descriptions
                    WHERE rv_product_id = %s""",
                (rv_product_id,),
            )
            data["descriptions_meta"] = [
                {"perspective": r[0], "desc_len": r[1], "has_bge": r[2], "has_openai": r[3]}
                for r in cur.fetchall()
            ]
            return _normalize_json(data)
    finally:
        conn.close()


def _normalize_json(d: dict) -> dict:
    """psycopg가 돌려준 datetime/Decimal 같은 비-JSON 친화 타입을 문자열로."""
    import datetime, decimal
    out = {}
    for k, v in d.items():
        if isinstance(v, (datetime.datetime, datetime.date)):
            out[k] = v.isoformat()
        elif isinstance(v, decimal.Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────
# 광고주 목록 (검색 페이지 dropdown 용)
# ─────────────────────────────────────────────────────────────
@router.get("/advertisers-rv")
def list_advertisers():
    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.id, a.name, a.host_type, a.shop_url,
                          COUNT(pe.rv_product_id) AS enriched_count
                     FROM advertisers a
                     LEFT JOIN product_enriched pe ON pe.advertiser_id = a.id
                    GROUP BY a.id, a.name, a.host_type, a.shop_url
                    ORDER BY a.id"""
            )
            rows = cur.fetchall()
            return [
                {"id": r[0], "name": r[1], "host_type": r[2], "shop_url": r[3], "enriched_count": r[4]}
                for r in rows
            ]
    finally:
        conn.close()


@router.get("/advertisers-rv/{advertiser_id}/stats")
def advertiser_stats(advertiser_id: int):
    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE scrape_status = 'scraped') AS scraped,
                       COUNT(*) FILTER (WHERE scrape_status != 'scraped') AS pending
                   FROM rv_products WHERE advertiser_id = %s""",
                (advertiser_id,),
            )
            r = cur.fetchone()
            total, scraped, pending = r[0], r[1], r[2]

            cur.execute(
                """SELECT
                       COUNT(*) AS llm_enriched,
                       COUNT(*) FILTER (WHERE enrich_status = 'embedded') AS vectorized
                   FROM product_enriched WHERE advertiser_id = %s""",
                (advertiser_id,),
            )
            r2 = cur.fetchone()
            llm_enriched, vectorized = r2[0], r2[1]

        return {
            "advertiser_id": advertiser_id,
            "total": total,
            "scraped": scraped,
            "pending": pending,
            "llm_enriched": llm_enriched,
            "vectorized": vectorized,
        }
    finally:
        conn.close()
