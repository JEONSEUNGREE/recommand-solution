"""검색 품질 평가 — Gecko(arXiv:2403.20327) / 오늘의집 방식.

흐름:
  1) build-set : gpt-4o-mini 가 상품 설명문에서 자연어 검색어를 생성한다.
     → 그 상품이 해당 쿼리의 gold(보장된 positive). (상품 50개 × 쿼리 3개 = 150개)
  2) run       : 각 쿼리로 검색기를 돌려 gold 가 얼마나 상위에 오는지 측정.
     → 메인 지표 NDCG@10, 보조 Recall@10 / MRR.

검색은 search_rv 의 dense 경로(4관점 평균거리 랭킹)를 그대로 재현하되,
_log_search 를 거치지 않아 search_logs 를 오염시키지 않는다. (평가는 부수효과 없음)

LLM 호출/cost 계산은 narrate·search_rv 와 동일한 gpt-4o-mini 단가를 쓴다.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from typing import Literal, Optional

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .search_rv import DB_DSN, _llm_parse_query

router = APIRouter(prefix="/eval")

OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
# gpt-4o-mini 단가 (narrate.py 와 동일): in $0.15/1M, out $0.60/1M
_GPT4O_MINI_IN = 0.00000015
_GPT4O_MINI_OUT = 0.0000006

_QUERY_GEN_SYSTEM = """\
당신은 한국 패션·라이프스타일 쇼핑몰의 검색 로그를 설계하는 전문가입니다.
하나의 상품과 그 상품의 다관점 설명문이 주어집니다.
이 상품을 찾으려는 실제 사용자가 검색창에 입력할 법한 **자연어 검색어**를 생성하세요.

규칙:
- 서로 다른 관점으로 다양하게: (상황/TPO), (소재·디테일·색감 등 속성), (대상·페르소나) 를 골고루.
- 너무 일반적이지 않게(예: "귀걸이" X), 그렇다고 상품명을 그대로 복붙하지도 말 것.
- 실제 사람이 치듯 자연스럽게. 1~12단어 내외.
- 한국어로만. 상품을 특정할 수 있을 만큼 구체적이되 키워드 나열이 아닌 자연어 문장/구.

반드시 아래 JSON 형식만 출력:
{"queries": ["검색어1", "검색어2", ...]}"""


# ─────────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────────
def _openai_client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY 미설정 (평가는 gpt-4o-mini 필요)")
    from openai import OpenAI
    return OpenAI()


def _gen_queries(client, product_name: str, descs: dict[str, str], n: int) -> tuple[list[str], dict]:
    """상품 설명문 → 검색어 n개 생성. (queries, usage) 반환."""
    persp_lines = "\n".join(f"- [{p}] {(d or '').strip()[:300]}" for p, d in descs.items() if d)
    user = (
        f"상품명: {product_name}\n다관점 설명문:\n{persp_lines}\n\n"
        f"위 상품을 찾을 법한 서로 다른 검색어 {n}개를 생성하세요."
    )
    r = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[
            {"role": "system", "content": _QUERY_GEN_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.8,
        response_format={"type": "json_object"},
        max_tokens=400,
    )
    usage = r.usage
    in_tok = usage.prompt_tokens if usage else 0
    out_tok = usage.completion_tokens if usage else 0
    cost = in_tok * _GPT4O_MINI_IN + out_tok * _GPT4O_MINI_OUT
    try:
        data = json.loads(r.choices[0].message.content)
        queries = [q.strip() for q in (data.get("queries") or []) if isinstance(q, str) and q.strip()]
    except Exception:
        queries = []
    return queries[:n], {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost}


def _embed_openai_batch(texts: list[str]) -> list[list[float]]:
    """OpenAI 임베딩 배치. search_rv._embed_openai 와 동일 모델."""
    if not texts:
        return []
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set on embedder process")
    from openai import OpenAI
    from .search_rv import OPENAI_MODEL
    client = OpenAI()
    out: list[list[float]] = []
    # OpenAI 임베딩 입력 상한 대비 청크 분할.
    for i in range(0, len(texts), 256):
        r = client.embeddings.create(model=OPENAI_MODEL, input=texts[i:i + 256])
        out.extend(d.embedding for d in r.data)
    return out


def _dense_rank(conn, qvec: list[float], backend: str, advertiser_id: Optional[int], k: int) -> list[int]:
    """search_rv 의 dense 경로와 동일: 4관점 평균거리로 상품 랭킹. rv_product_id 리스트(상위 k)."""
    col = "embedding_openai" if backend == "openai" else "embedding_bge"
    where = [f"pd.{col} IS NOT NULL"]
    params: list = [qvec]
    if advertiser_id is not None:
        where.append("pd.advertiser_id = %s")
        params.append(advertiser_id)
    sql = f"""
        WITH all_dist AS (
            SELECT pd.rv_product_id, pd.{col} <=> %s::vector AS distance
              FROM product_descriptions pd
             WHERE {' AND '.join(where)}
        ),
        ranked AS (
            SELECT rv_product_id, AVG(distance) AS distance
              FROM all_dist GROUP BY rv_product_id
        )
        SELECT rv_product_id FROM ranked ORDER BY distance ASC LIMIT %s
    """
    params.append(k)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]


# ─────────────────────────────────────────────────────────────
# 1) 평가셋 생성
# ─────────────────────────────────────────────────────────────
class BuildSetReq(BaseModel):
    advertiser_id: Optional[int] = None
    n_products: int = 50
    n_per_product: int = 3
    name: Optional[str] = None


@router.post("/build-set")
def build_set(req: BuildSetReq):
    """상품 N개를 랜덤 샘플 → 각 상품에서 gpt-4o-mini 로 쿼리 M개 생성 → eval_set 저장."""
    client = _openai_client()
    n_products = max(1, min(300, req.n_products))
    n_per = max(1, min(10, req.n_per_product))

    conn = psycopg.connect(DB_DSN)
    try:
        # bge 임베딩이 있는 상품만 샘플 (검색 대상과 동일 모집단)
        where = ["pd.embedding_bge IS NOT NULL"]
        params: list = []
        if req.advertiser_id is not None:
            where.append("pd.advertiser_id = %s")
            params.append(req.advertiser_id)
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT DISTINCT pd.rv_product_id
                      FROM product_descriptions pd
                     WHERE {' AND '.join(where)}""",
                params,
            )
            all_ids = [r[0] for r in cur.fetchall()]
        if not all_ids:
            raise HTTPException(400, "샘플할 상품이 없습니다 (embedding_bge 보유 상품 0)")
        random.shuffle(all_ids)
        sample_ids = all_ids[:n_products]

        # 설명문 로드
        with conn.cursor() as cur:
            cur.execute(
                """SELECT pd.rv_product_id, pd.perspective, pd.description,
                          rv.product_name, pd.advertiser_id
                     FROM product_descriptions pd
                     JOIN rv_products rv ON rv.id = pd.rv_product_id
                    WHERE pd.rv_product_id = ANY(%s)""",
                (sample_ids,),
            )
            rows = cur.fetchall()
        prod: dict[int, dict] = {}
        for rv_id, persp, desc, pname, adv in rows:
            p = prod.setdefault(rv_id, {"name": pname, "advertiser_id": adv, "descs": {}})
            p["descs"][persp] = desc

        # eval_set 생성
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO eval_sets (name, advertiser_id, n_products, n_per_product, llm_model)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (req.name, req.advertiser_id, len(sample_ids), n_per, OPENAI_CHAT_MODEL),
            )
            set_id = cur.fetchone()[0]
        conn.commit()

        total_cost = 0.0
        total_in = total_out = 0
        n_queries = 0
        failures = 0
        for rv_id in sample_ids:
            info = prod.get(rv_id)
            if not info:
                continue
            try:
                queries, usage = _gen_queries(client, info["name"] or "", info["descs"], n_per)
            except Exception:
                failures += 1
                continue
            total_cost += usage["cost_usd"]
            total_in += usage["input_tokens"]
            total_out += usage["output_tokens"]
            if not queries:
                failures += 1
                continue
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(
                        """INSERT INTO eval_queries (eval_set_id, rv_product_id, advertiser_id, query)
                           VALUES (%s, %s, %s, %s)""",
                        (set_id, rv_id, info["advertiser_id"], q),
                    )
                    n_queries += 1
            conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE eval_sets SET n_queries = %s, llm_cost_usd = %s WHERE id = %s",
                (n_queries, round(total_cost, 8), set_id),
            )
        conn.commit()

        return {
            "eval_set_id": set_id,
            "n_products": len(sample_ids),
            "n_per_product": n_per,
            "n_queries": n_queries,
            "failures": failures,
            "llm_model": OPENAI_CHAT_MODEL,
            "usage": {"input_tokens": total_in, "output_tokens": total_out,
                      "cost_usd": round(total_cost, 8)},
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 2) 평가 실행 (검색 → NDCG@10 / Recall@10 / MRR)
# ─────────────────────────────────────────────────────────────
class RunReq(BaseModel):
    eval_set_id: int
    backend: Literal["bge", "openai"] = "bge"
    k: int = 10                          # 검색 top-k (지표는 @10 고정)
    llm_query_parse: bool = False        # 검색 전 LLM(gpt-4o-mini) 쿼리 정제 사용 여부


def _ndcg_at_10(gold_rank: Optional[int]) -> float:
    """gold 1개(이진 관련성) 기준 NDCG@10. IDCG=1 이므로 NDCG=DCG."""
    if gold_rank is None or gold_rank > 10:
        return 0.0
    return 1.0 / math.log2(gold_rank + 1)


@router.post("/run")
def run_eval(req: RunReq):
    t0 = time.time()
    k = max(10, min(100, req.k))  # 지표가 @10 이므로 최소 10 이상 검색
    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT advertiser_id FROM eval_sets WHERE id = %s", (req.eval_set_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"eval_set {req.eval_set_id} 없음")
            set_advertiser = row[0]
            cur.execute(
                "SELECT id, rv_product_id, advertiser_id, query FROM eval_queries WHERE eval_set_id = %s ORDER BY id",
                (req.eval_set_id,),
            )
            queries = cur.fetchall()
        if not queries:
            raise HTTPException(400, "평가셋에 쿼리가 없습니다. 먼저 build-set 을 실행하세요.")

        client = _openai_client() if req.llm_query_parse else None  # 미설정 시 여기서 친절히 실패

        # run 헤더 먼저 생성
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO eval_runs
                   (eval_set_id, backend, mode, llm_query_parse, k, n_queries)
                   VALUES (%s, %s, 'dense', %s, %s, %s) RETURNING id""",
                (req.eval_set_id, req.backend, req.llm_query_parse, k, len(queries)),
            )
            run_id = cur.fetchone()[0]
        conn.commit()

        # 1차: 검색에 쓸 텍스트 확정(선택적 LLM 정제) → 2차에서 배치 임베딩.
        search_texts: list[str] = []
        for _q_id, _gold, _adv, q_text in queries:
            t = q_text
            if req.llm_query_parse:
                try:
                    t = _llm_parse_query(q_text, "openai").get("semantic_query") or q_text
                except Exception:
                    t = q_text
            search_texts.append(t)

        # 배치 임베딩 — 쿼리마다 개별 호출하면 CPU bge 가 매우 느려 한 번에 인코딩.
        if req.backend == "openai":
            qvecs = _embed_openai_batch(search_texts)
        else:
            from . import bge_model
            qvecs = bge_model.encode_dense(search_texts)

        sum_ndcg = sum_rr = 0.0
        hits = 0
        for (q_id, gold_id, q_adv, q_text), qvec in zip(queries, qvecs):
            adv = q_adv if q_adv is not None else set_advertiser
            ranked = _dense_rank(conn, qvec, req.backend, adv, k)

            gold_rank = None
            for idx, rid in enumerate(ranked):
                if rid == gold_id:
                    gold_rank = idx + 1
                    break
            ndcg = _ndcg_at_10(gold_rank)
            rr = (1.0 / gold_rank) if gold_rank else 0.0
            hit = gold_rank is not None and gold_rank <= 10
            sum_ndcg += ndcg
            sum_rr += rr
            hits += 1 if hit else 0

            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO eval_run_items
                       (eval_run_id, eval_query_id, query, gold_rv_product_id,
                        gold_rank, ndcg_at_10, reciprocal_rank, hit, top_ids)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (run_id, q_id, q_text, gold_id, gold_rank,
                     round(ndcg, 4), round(rr, 4), hit, ranked[:10]),
                )
            conn.commit()

        n = len(queries)
        ndcg_avg = round(sum_ndcg / n, 4)
        recall_avg = round(hits / n, 4)
        mrr_avg = round(sum_rr / n, 4)
        dur = int((time.time() - t0) * 1000)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE eval_runs
                      SET ndcg_at_10 = %s, recall_at_10 = %s, mrr = %s, duration_ms = %s
                    WHERE id = %s""",
                (ndcg_avg, recall_avg, mrr_avg, dur, run_id),
            )
        conn.commit()

        return {
            "eval_run_id": run_id,
            "eval_set_id": req.eval_set_id,
            "backend": req.backend,
            "llm_query_parse": req.llm_query_parse,
            "k": k,
            "n_queries": n,
            "ndcg_at_10": ndcg_avg,
            "recall_at_10": recall_avg,
            "mrr": mrr_avg,
            "duration_ms": dur,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 3) 조회
# ─────────────────────────────────────────────────────────────
@router.get("/sets")
def list_sets():
    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.id, s.name, s.advertiser_id, s.n_products, s.n_per_product,
                          s.n_queries, s.llm_model, s.llm_cost_usd, s.created_at,
                          (SELECT count(*) FROM eval_runs r WHERE r.eval_set_id = s.id) AS run_count,
                          (SELECT max(r.ndcg_at_10) FROM eval_runs r WHERE r.eval_set_id = s.id) AS best_ndcg
                     FROM eval_sets s ORDER BY s.id DESC LIMIT 100"""
            )
            cols = [c.name for c in cur.description]
            sets = [dict(zip(cols, r)) for r in cur.fetchall()]
        for s in sets:
            s["llm_cost_usd"] = float(s["llm_cost_usd"]) if s["llm_cost_usd"] is not None else 0.0
            s["best_ndcg"] = float(s["best_ndcg"]) if s["best_ndcg"] is not None else None
            s["created_at"] = s["created_at"].isoformat() if s["created_at"] else None
        return {"sets": sets}
    finally:
        conn.close()


@router.get("/sets/{set_id}/runs")
def list_runs(set_id: int):
    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, backend, mode, llm_query_parse, k, n_queries,
                          ndcg_at_10, recall_at_10, mrr, duration_ms, created_at
                     FROM eval_runs WHERE eval_set_id = %s ORDER BY id DESC""",
                (set_id,),
            )
            cols = [c.name for c in cur.description]
            runs = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in runs:
            for m in ("ndcg_at_10", "recall_at_10", "mrr"):
                r[m] = float(r[m]) if r[m] is not None else None
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        return {"runs": runs}
    finally:
        conn.close()


@router.get("/runs/{run_id}/items")
def run_items(run_id: int):
    """쿼리별 상세 — gold 순위·NDCG. 낮은 NDCG(검색 실패) 부터."""
    conn = psycopg.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ri.query, ri.gold_rv_product_id, ri.gold_rank, ri.ndcg_at_10,
                          ri.reciprocal_rank, ri.hit, ri.top_ids, rv.product_name
                     FROM eval_run_items ri
                     LEFT JOIN rv_products rv ON rv.id = ri.gold_rv_product_id
                    WHERE ri.eval_run_id = %s
                    ORDER BY ri.ndcg_at_10 ASC, ri.id ASC""",
                (run_id,),
            )
            cols = [c.name for c in cur.description]
            items = [dict(zip(cols, r)) for r in cur.fetchall()]
        for it in items:
            it["ndcg_at_10"] = float(it["ndcg_at_10"]) if it["ndcg_at_10"] is not None else 0.0
            it["reciprocal_rank"] = float(it["reciprocal_rank"]) if it["reciprocal_rank"] is not None else 0.0
        return {"items": items}
    finally:
        conn.close()
