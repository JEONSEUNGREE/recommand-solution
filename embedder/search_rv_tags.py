"""태그 강화 검색 API — 기존 /search-rv-llm 과 별개의 실험 채널.

5채널 융합:  description dense + sparse + morpheme (기존 3채널)
           + tags dense (신규: product_enriched.tags_embedding_bge)

기존 search_rv.py / product_descriptions 는 절대 수정하지 않는다.
격리 보장을 위해 SQL WHERE 에 pd.perspective IN (...4가지...) 명시.
"""
from __future__ import annotations

import json
import os
import time
from typing import Literal, Optional

import psycopg
from fastapi import APIRouter
from pydantic import BaseModel

from .search_rv import (
    DB_DSN,
    _KEYWORD_BLOCKLIST,
    _colbert_rerank,
    _embed_bge,
    _embed_colbert,
    _embed_morpheme,
    _embed_sparse,
    _llm_parse_query,
    _log_search,
    _project_for_viz,
)

router = APIRouter()

# 기존 4관점으로 명시 제한 — tags 행이 product_descriptions에 추가돼도 격리
_PERSP_IN = "pd.perspective IN ('situation','material','style','persona')"


def _make_tag_text(tags_json) -> str:
    """product_enriched.tags jsonb → 임베딩용 텍스트.

    confidence 0.7 이상 태그를 confidence 내림차순으로 공백 연결.
    예: "캐주얼룩 데일리 세련된 트렌디 미니멀 편안한핏 비건소재"
    """
    if not tags_json:
        return ""
    try:
        tags = sorted(tags_json, key=lambda t: -float(t.get("confidence", 0)))
        return " ".join(t["tag"] for t in tags if float(t.get("confidence", 0)) >= 0.7)
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# 요청 모델
# ─────────────────────────────────────────────────────────────
class SearchRvTagsReq(BaseModel):
    query: str
    advertiser_id: Optional[int] = None
    backend: Literal["bge"] = "bge"           # tags 채널은 BGE-M3 전용
    llm_provider: Literal["groq", "openai"] = "openai"
    k: int = 10
    version: str = "1.1"
    mode: Literal["dense", "hybrid"] = "dense"
    fusion: Literal["rrf", "alpha"] = "rrf"
    alpha: float = 0.5
    use_morpheme: bool = False
    w_dense: Optional[float] = None
    w_sparse: Optional[float] = None
    w_morpheme: Optional[float] = None
    w_tags: Optional[float] = None            # None → 자동 (dense=0.3 / hybrid=0.25)
    use_colbert: bool = False
    colbert_top: int = 50


# ─────────────────────────────────────────────────────────────
# 메인 검색 엔드포인트
# ─────────────────────────────────────────────────────────────
@router.post("/search-rv-tags-llm")
def search_rv_tags_llm(req: SearchRvTagsReq):
    """태그 채널 포함 5채널 검색 (기존 /search-rv-llm 과 완전 독립)."""
    t0 = time.time()

    # ── LLM 쿼리 파싱 ────────────────────────────────────────
    t_llm = time.time()
    parsed = _llm_parse_query(req.query, req.llm_provider)
    llm_duration_ms = int((time.time() - t_llm) * 1000)
    semantic_query = parsed.get("semantic_query") or req.query
    filters = parsed.get("filters") or {}

    # ── 임베딩 ───────────────────────────────────────────────
    t_embed = time.time()
    qvec = _embed_bge(semantic_query)

    hybrid = req.mode == "hybrid"
    hybrid_note = None
    qsparse = qmorph = None
    use_morph = bool(req.use_morpheme)

    if hybrid:
        qsparse = _embed_sparse(semantic_query)
        if use_morph:
            qmorph = _embed_morpheme(semantic_query)
            if qmorph is None:
                use_morph = False
                hybrid_note = "형태소 서비스 없음 — sparse만 사용"
        if qsparse is None and qmorph is None:
            hybrid = False
            hybrid_note = "sparse-embedder 없음 — dense 폴백"

    use_s = qsparse is not None
    use_m = use_morph and qmorph is not None

    use_colbert = bool(req.use_colbert)
    qcolbert = None
    colbert_note = None
    if use_colbert:
        qcolbert = _embed_colbert(semantic_query)
        if qcolbert is None:
            use_colbert = False
            colbert_note = "colbert-reranker 없음 — 1차 순서 유지"
    cand_k = max(req.k, int(req.colbert_top)) if use_colbert else req.k

    embed_duration_ms = int((time.time() - t_embed) * 1000)

    # ── 채널별 가중치 결정 ────────────────────────────────────
    def _resolve_weights() -> tuple[float, float, float, float]:
        """(w_dense, w_sparse, w_morph, w_tags) — 합=1 보장."""
        wt = req.w_tags if req.w_tags is not None else (0.40 if hybrid else 0.50)
        remain = 1.0 - wt

        if not hybrid:
            # dense + tags 두 채널
            return remain, 0.0, 0.0, wt

        # hybrid: dense + sparse + morph + tags
        wm = (remain / 3.0) if use_m else 0.0
        wd_base = req.alpha * (remain - wm)
        ws_base = (1.0 - req.alpha) * (remain - wm)

        wd = wd_base if use_s or True else remain - wm   # dense 항상 있음
        ws = ws_base if use_s else 0.0
        if not use_m:
            wm = 0.0

        tot = wd + ws + wm + wt
        if tot <= 0:
            return 1.0, 0.0, 0.0, 0.0
        return wd / tot, ws / tot, wm / tot, wt / tot

    w_dense, w_sparse, w_morph, w_tags = _resolve_weights()

    # ── WHERE 절 구성 ─────────────────────────────────────────
    where: list[str] = [
        "pd.embedding_bge IS NOT NULL",
        _PERSP_IN,
    ]
    params: list = []

    if req.advertiser_id is not None:
        where.append("pd.advertiser_id = %s")
        params.append(req.advertiser_id)

    rv_price_join = ""
    if filters.get("price_min") is not None or filters.get("price_max") is not None:
        rv_price_join = "JOIN rv_products rv_p ON rv_p.id = pd.rv_product_id"
        if filters.get("price_min") is not None:
            where.append("COALESCE(rv_p.sale_price, rv_p.price) >= %s")
            params.append(int(filters["price_min"]))
        if filters.get("price_max") is not None:
            where.append("COALESCE(rv_p.sale_price, rv_p.price) <= %s")
            params.append(int(filters["price_max"]))

    pe_join = "LEFT JOIN product_enriched pe ON pe.rv_product_id = pd.rv_product_id"

    if filters.get("category_contains"):
        where.append("pe.category ILIKE %s")
        params.append(f"%{filters['category_contains']}%")

    # endorser 미검증 상품 제외
    where.append(
        "NOT EXISTS (SELECT 1 FROM jsonb_array_elements("
        "CASE WHEN jsonb_typeof(pe.tags)='array' THEN pe.tags ELSE '[]'::jsonb END"
        ") t WHERE t->>'tag_category'='endorser' "
        "AND COALESCE(t->>'verified','false') <> 'true')"
    )

    base_where = where.copy()
    base_params = params.copy()

    pe_where: list[str] = []
    if filters.get("target_gender"):
        pe_where.append("pe.target_gender = %s")
        params.append(filters["target_gender"])
    if filters.get("target_age_min") is not None:
        pe_where.append("pe.target_age_max >= %s")
        params.append(int(filters["target_age_min"]))
    if filters.get("target_age_max") is not None:
        pe_where.append("pe.target_age_min <= %s")
        params.append(int(filters["target_age_max"]))
    if pe_where:
        where.append("(" + " AND ".join(pe_where) + ")")

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

    # ── 컬럼 목록 ────────────────────────────────────────────
    cols_h = [
        "rv_product_id", "advertiser_id", "perspective", "description",
        "dense_dist", "sparse_ip", "morph_ip", "tags_dist",
        "product_code", "product_name", "product_url", "image_url",
        "price", "sale_price",
        "category", "brand", "brand_tier", "target_gender",
        "target_age_min", "target_age_max", "tags", "desc_persona", "emb",
    ]

    # ── 5채널 RRF/alpha 융합 ──────────────────────────────────
    def _fuse(rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        K = 60
        n = len(rows)
        has_tags = any(r.get("tags_dist") is not None for r in rows)

        if req.fusion == "rrf":
            for i, c in enumerate(sorted(rows, key=lambda x: x["dense_dist"]), 1):
                c["rank_dense"] = i
            if use_s:
                for i, c in enumerate(sorted(rows, key=lambda x: x["sparse_ip"]), 1):
                    c["rank_sparse"] = i
            if use_m:
                for i, c in enumerate(sorted(rows, key=lambda x: x["morph_ip"]), 1):
                    c["rank_morph"] = i
            if has_tags:
                for i, c in enumerate(
                    sorted(rows, key=lambda x: (x.get("tags_dist") is None,
                                                x.get("tags_dist") or 1.0)), 1
                ):
                    c["rank_tags"] = i
            for c in rows:
                s = w_dense / (K + c["rank_dense"])
                if use_s:
                    s += w_sparse / (K + c.get("rank_sparse", n))
                if use_m:
                    s += w_morph / (K + c.get("rank_morph", n))
                if has_tags:
                    s += w_tags / (K + c.get("rank_tags", n))
                c["score"] = s
        else:  # alpha 가중합
            dlo, dhi = (lambda v: (min(v), max(v)))([c["dense_dist"] for c in rows])
            slo, shi = (0.0, 1.0)
            if use_s:
                sv = [c["sparse_ip"] for c in rows]
                slo, shi = min(sv), max(sv)
            mlo, mhi = (0.0, 1.0)
            if use_m:
                mv = [c["morph_ip"] for c in rows]
                mlo, mhi = min(mv), max(mv)
            tlo, thi = (0.0, 1.0)
            if has_tags:
                tv = [c["tags_dist"] for c in rows if c.get("tags_dist") is not None]
                if tv:
                    tlo, thi = min(tv), max(tv)

            for c in rows:
                dn = (dhi - c["dense_dist"]) / (dhi - dlo) if dhi > dlo else 1.0
                s = w_dense * dn
                if use_s and shi > slo:
                    s += w_sparse * (shi - c["sparse_ip"]) / (shi - slo)
                if use_m and mhi > mlo:
                    s += w_morph * (mhi - c["morph_ip"]) / (mhi - mlo)
                if has_tags and c.get("tags_dist") is not None and thi > tlo:
                    s += w_tags * (thi - c["tags_dist"]) / (thi - tlo)
                c["score"] = s

        # 상품별 최고 관점 대표
        groups: dict = {}
        for c in rows:
            groups.setdefault(c["rv_product_id"], []).append(c)
        out = []
        for grp in groups.values():
            rep = max(grp, key=lambda x: x["score"])
            db = min(grp, key=lambda x: x["dense_dist"])
            rep["dense_best_perspective"] = db["perspective"]
            rep["dense_best_description"] = db["description"]
            rep["dense_best_dist"] = db["dense_dist"]
            if use_s:
                sb = min(grp, key=lambda x: x["sparse_ip"])
                rep["sparse_best_perspective"] = sb["perspective"]
                rep["sparse_best_description"] = sb["description"]
                rep["sparse_best_ip"] = sb["sparse_ip"]
            if use_m:
                mb = min(grp, key=lambda x: x["morph_ip"])
                rep["morph_best_perspective"] = mb["perspective"]
                rep["morph_best_description"] = mb["description"]
                rep["morph_best_ip"] = mb["morph_ip"]
            out.append(rep)
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:cand_k]

    # ── SQL 실행 ─────────────────────────────────────────────
    def _run_query(conn, where_list: list, where_params: list) -> list:
        # tags_dist: pe.tags_embedding_bge가 NULL이면 NULL 반환 → Python에서 처리
        if hybrid:
            sel_sparse = ("pd.embedding_sparse <#> %s::sparsevec AS sparse_ip,"
                          if use_s else "0.0 AS sparse_ip,")
            sel_morph  = ("pd.embedding_morpheme <#> %s::sparsevec AS morph_ip,"
                          if use_m else "0.0 AS morph_ip,")
            vec_params: list = [qvec]
            if use_s:
                vec_params.append(qsparse)
            if use_m:
                vec_params.append(qmorph)
            vec_params.append(qvec)   # tags_dist 계산용
            sql = f"""
                WITH all_dist AS (
                    SELECT pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
                           pd.embedding_bge <=> %s::vector AS dense_dist,
                           {sel_sparse}
                           {sel_morph}
                           pe.tags_embedding_bge <=> %s::vector AS tags_dist,
                           pd.embedding_bge::text AS emb
                      FROM product_descriptions pd
                      {rv_price_join}
                      {pe_join}
                     WHERE {' AND '.join(where_list)}
                )
                SELECT a.rv_product_id, a.advertiser_id, a.perspective, a.description,
                       a.dense_dist, a.sparse_ip, a.morph_ip, a.tags_dist,
                       rv.product_code, rv.product_name, rv.product_url, rv.image_url,
                       rv.price, rv.sale_price,
                       pe2.category, pe2.brand, pe2.brand_tier, pe2.target_gender,
                       pe2.target_age_min, pe2.target_age_max, pe2.tags, pe2.desc_persona,
                       a.emb
                  FROM all_dist a
                  JOIN rv_products rv ON rv.id = a.rv_product_id
                  LEFT JOIN product_enriched pe2 ON pe2.rv_product_id = a.rv_product_id
            """
            all_params = vec_params + where_params
        else:
            # dense + tags 두 채널
            sql = f"""
                WITH all_dist AS (
                    SELECT pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
                           pd.embedding_bge <=> %s::vector AS dense_dist,
                           0.0 AS sparse_ip,
                           0.0 AS morph_ip,
                           pe.tags_embedding_bge <=> %s::vector AS tags_dist,
                           pd.embedding_bge::text AS emb
                      FROM product_descriptions pd
                      {rv_price_join}
                      {pe_join}
                     WHERE {' AND '.join(where_list)}
                )
                SELECT a.rv_product_id, a.advertiser_id, a.perspective, a.description,
                       a.dense_dist, a.sparse_ip, a.morph_ip, a.tags_dist,
                       rv.product_code, rv.product_name, rv.product_url, rv.image_url,
                       rv.price, rv.sale_price,
                       pe2.category, pe2.brand, pe2.brand_tier, pe2.target_gender,
                       pe2.target_age_min, pe2.target_age_max, pe2.tags, pe2.desc_persona,
                       a.emb
                  FROM all_dist a
                  JOIN rv_products rv ON rv.id = a.rv_product_id
                  LEFT JOIN product_enriched pe2 ON pe2.rv_product_id = a.rv_product_id
            """
            all_params = [qvec, qvec] + where_params

        with conn.cursor() as cur:
            cur.execute(sql, all_params)
            rows = cur.fetchall()

        cands = []
        for r in rows:
            d = dict(zip(cols_h, r))
            d["dense_dist"] = float(d["dense_dist"])
            d["sparse_ip"]  = float(d["sparse_ip"])
            d["morph_ip"]   = float(d["morph_ip"])
            d["tags_dist"]  = float(d["tags_dist"]) if d.get("tags_dist") is not None else None
            cands.append(d)

        fused = _fuse(cands)
        for d in fused:
            d["distance"] = d["dense_dist"]
        return fused

    conn = psycopg.connect(DB_DSN)
    try:
        keyword_relaxed = False
        pe_filter_relaxed = False

        if kw_clause:
            items = _run_query(conn, where + [kw_clause], params + kw_params)
            if not items:
                items = _run_query(conn, where, params)
                keyword_relaxed = True
        else:
            items = _run_query(conn, where, params)

        if not items and pe_where:
            items = _run_query(conn, base_where, base_params)
            pe_filter_relaxed = True

        colbert_applied = False
        if use_colbert and qcolbert and items:
            items = _colbert_rerank(qcolbert, items, conn)
            colbert_applied = True
        items = items[:req.k]

        # PCA 3D 시각화
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
                print(f"[search_rv_tags PCA] {e}", file=sys.stderr)
            finally:
                for it in items:
                    it.pop("emb", None)

        # 백엔드 레이블
        if hybrid:
            sig = "ds" + ("m" if use_m else "")
            backend_label = (f"bge+{sig}+tags:{req.fusion}"
                             f"(d={w_dense:.2g},s={w_sparse:.2g},t={w_tags:.2g})")
        else:
            backend_label = f"bge+tags(d={w_dense:.2g},t={w_tags:.2g})"
        if colbert_applied:
            backend_label += "+colbert"

        total_ms = int((time.time() - t0) * 1000)
        _usage = parsed.get("_usage") or {}
        _parsed_for_log = {k: v for k, v in parsed.items() if k != "_usage"}

        log_id = _log_search(
            query=req.query, semantic_query=semantic_query, llm_used=True,
            llm_provider=req.llm_provider, backend=backend_label,
            advertiser_id=req.advertiser_id, result_count=len(items),
            keyword_filters=keywords, keyword_filter_relaxed=keyword_relaxed or pe_filter_relaxed,
            items=items,
            llm_duration_ms=llm_duration_ms, embed_duration_ms=embed_duration_ms,
            total_duration_ms=total_ms,
            llm_input_tokens=_usage.get("input_tokens"),
            llm_output_tokens=_usage.get("output_tokens"),
            llm_cost_usd=_usage.get("cost_usd"),
            llm_parsed_json=_parsed_for_log,
            version=req.version,
        )

        return {
            "log_id": log_id,
            "backend": "bge",
            "channel": "tags",           # chat2.html이 구분용으로 사용
            "mode": "hybrid" if hybrid else "dense",
            "fusion": req.fusion if hybrid else None,
            "alpha": req.alpha if hybrid else None,
            "use_morpheme": use_m,
            "weights": {
                "dense": round(w_dense, 3),
                "sparse": round(w_sparse, 3),
                "morpheme": round(w_morph, 3),
                "tags": round(w_tags, 3),
            },
            "use_colbert": colbert_applied,
            "colbert_top": cand_k if colbert_applied else None,
            "hybrid_note": hybrid_note,
            "colbert_note": colbert_note,
            "dim": 1024,
            "advertiser_id": req.advertiser_id,
            "k": req.k,
            "raw_query": req.query,
            "corrected_query": parsed.get("corrected_query") or req.query,
            "parsed": parsed,
            "keyword_filters_applied": keywords,
            "keyword_filter_relaxed": keyword_relaxed,
            "pe_filter_relaxed": pe_filter_relaxed,
            "items": items,
            "query_coord3d": query_coord3d,
            "llm_duration_ms": llm_duration_ms,
            "embed_duration_ms": embed_duration_ms,
            "total_duration_ms": total_ms,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 태그 임베딩 일괄 생성 (관리용 엔드포인트)
# ─────────────────────────────────────────────────────────────
@router.post("/admin/build-tag-embeddings")
def build_tag_embeddings(force: bool = False):
    """product_enriched.tags → tags_embedding_bge 일괄 생성.

    force=True 이면 기존 값도 덮어씀. 기본은 NULL인 행만 처리.
    처리 결과(done/skipped/failed/total)를 반환.
    """
    from . import bge_model

    conn = psycopg.connect(DB_DSN)
    done = skipped = failed = 0
    try:
        null_cond = "" if force else "AND tags_embedding_bge IS NULL"
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT rv_product_id, tags FROM product_enriched
                    WHERE tags IS NOT NULL AND jsonb_array_length(tags) > 0
                    {null_cond}
                    ORDER BY rv_product_id"""
            )
            rows = cur.fetchall()

        total = len(rows)
        batch_size = 32

        for i in range(0, total, batch_size):
            batch = rows[i : i + batch_size]
            pids  = [r[0] for r in batch]
            texts = [_make_tag_text(r[1]) for r in batch]

            # 빈 텍스트(태그 전부 confidence < 0.7) 건너뜀
            valid_idx = [j for j, t in enumerate(texts) if t.strip()]
            skipped += len(batch) - len(valid_idx)
            if not valid_idx:
                continue

            v_pids  = [pids[j]  for j in valid_idx]
            v_texts = [texts[j] for j in valid_idx]

            try:
                vecs = bge_model.encode_dense(v_texts)  # list[list[float]]
                with conn.cursor() as cur:
                    for pid, vec in zip(v_pids, vecs):
                        cur.execute(
                            "UPDATE product_enriched"
                            "   SET tags_embedding_bge = %s::vector"
                            " WHERE rv_product_id = %s",
                            (str(vec), pid),
                        )
                conn.commit()
                done += len(v_pids)
            except Exception as e:
                conn.rollback()
                failed += len(v_pids)
                import sys
                print(f"[build_tag_emb] batch {i} 실패: {e}", file=sys.stderr)

        return {
            "ok": True,
            "total": total,
            "done": done,
            "skipped": skipped,
            "failed": failed,
        }
    finally:
        conn.close()
