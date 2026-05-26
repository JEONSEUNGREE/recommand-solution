"""
Enrich 상품 목록 + 상세 조회 API.

GET /products-enriched           - 페이지네이션 목록 (상품명 LIKE, 태그 필터, 카테고리 필터)
GET /products-enriched/count     - 전체 / embedded 건수
GET /products-enriched/categories - 카테고리 목록 (embedded 기준)
GET /products-enriched/{rv_id}   - 단건 상세

GET /search-logs                 - 검색 로그 목록 (페이지네이션, zero-result 필터)
"""

import json
import os
from pathlib import Path
from typing import Optional

import psycopg
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg.rows import dict_row

DB_DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")

router = APIRouter(prefix="/products-enriched", tags=["products"])


def _conn():
    return psycopg.connect(DB_DSN, row_factory=dict_row)


@router.get("/endorsers")
def list_endorsers():
    """endorser 태그가 있는 상품 전체 목록 (검수용)"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT pe.rv_product_id, rp.product_name, rp.product_code,
                   rp.image_url, rp.product_url, pe.tags, pe.desc_persona
              FROM product_enriched pe
              JOIN rv_products rp ON rp.id = pe.rv_product_id
             WHERE pe.tags::text LIKE '%endorser%'
             ORDER BY pe.rv_product_id
        """)
        return cur.fetchall()


@router.patch("/{rv_id}/endorser-tag")
def patch_endorser_tag(
    rv_id: int,
    tag_name: str = Body(...),
    gender: str = Body(...),
    verified: bool = Body(False),
):
    """endorser 태그의 gender / verified 수정 후 재임베딩"""
    from embedder.embed_descriptions import embed_one

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tags, desc_persona FROM product_enriched WHERE rv_product_id=%s",
            (rv_id,),
        )
        row = cur.fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="not found")

        old_tags = row["tags"] or []
        new_tags = []
        for t in old_tags:
            if t.get("tag_category") == "endorser" and t.get("tag") == tag_name:
                new_tags.append({**t, "gender": gender, "verified": verified})
            else:
                new_tags.append(t)

        # desc_persona 재구성
        old_persona = row["desc_persona"] or ""
        bare = old_persona
        if "착용 상품입니다." in bare:
            idx = bare.index("착용 상품입니다.")
            bare = bare[idx + len("착용 상품입니다."):].strip()

        endorser_tags = [t for t in new_tags if t.get("tag_category") == "endorser"]
        who = " ".join(t["tag"] for t in endorser_tags)
        # 대표 gender: 첫 번째 그룹/아티스트 태그 기준
        rep_gender = endorser_tags[0]["gender"] if endorser_tags else "불명"
        gender_label = {"여성": "여성 연예인", "남성": "남성 연예인", "혼성": "혼성 그룹"}.get(rep_gender, "연예인")
        new_persona = f"{who}({gender_label}) 착용 상품입니다. {bare}".strip()

        cur.execute(
            "UPDATE product_enriched SET tags=%s::jsonb, desc_persona=%s WHERE rv_product_id=%s",
            (json.dumps(new_tags, ensure_ascii=False), new_persona, rv_id),
        )

    raw_conn = psycopg.connect(DB_DSN)
    try:
        embed_one(raw_conn, rv_id, "bge")
    finally:
        raw_conn.close()

    return {"ok": True, "rv_product_id": rv_id, "tag": tag_name, "gender": gender, "verified": verified}


@router.get("/categories")
def list_categories():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT category, COUNT(*) AS cnt
              FROM product_enriched
             WHERE enrich_status = 'embedded' AND category IS NOT NULL
             GROUP BY category
             ORDER BY cnt DESC, category
        """)
        return cur.fetchall()


@router.get("/token-stats")
def get_token_stats(advertiser_id: Optional[int] = Query(None)):
    """상품 LLM 정제 시 사용된 토큰/비용 집계 (product_enriched 기준)."""
    adv_cond = "AND pe.advertiser_id = %(adv)s" if advertiser_id else ""
    p = {"adv": advertiser_id}
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                COUNT(*)                                             AS total_enriched,
                COALESCE(SUM(pe.input_tokens), 0)                   AS total_input_tokens,
                COALESCE(SUM(pe.output_tokens), 0)                  AS total_output_tokens,
                COALESCE(SUM(pe.cache_read_input_tokens), 0)        AS total_cache_read_tokens,
                COALESCE(SUM(pe.cache_creation_input_tokens), 0)    AS total_cache_write_tokens,
                COALESCE(SUM(pe.cost_usd), 0)                       AS total_cost_usd,
                COALESCE(AVG(pe.cost_usd) FILTER (WHERE pe.cost_usd IS NOT NULL AND pe.cost_usd > 0), 0) AS avg_cost_usd,
                COALESCE(AVG(pe.duration_ms) FILTER (WHERE pe.duration_ms IS NOT NULL), 0)               AS avg_duration_ms,
                COUNT(DISTINCT pe.model_used) FILTER (WHERE pe.model_used IS NOT NULL)                   AS model_count
              FROM product_enriched pe
             WHERE pe.enrich_status = 'embedded'
               {adv_cond}
        """, p)
        row = cur.fetchone()

        cur.execute(f"""
            SELECT pe.model_used, COUNT(*) AS cnt,
                   COALESCE(SUM(pe.cost_usd), 0) AS cost_usd
              FROM product_enriched pe
             WHERE pe.enrich_status = 'embedded' AND pe.model_used IS NOT NULL
               {adv_cond}
             GROUP BY pe.model_used
             ORDER BY cost_usd DESC
        """, p)
        by_model = cur.fetchall()

    return {**row, "by_model": by_model}


@router.get("/costs")
def list_product_costs(
    advertiser_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="상품명 검색"),
    order_by: str = Query("cost_usd", description="정렬 기준: cost_usd | output_tokens | duration_ms"),
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=200),
):
    """상품별 LLM 정제 비용 목록."""
    safe_order = {"cost_usd", "output_tokens", "input_tokens", "duration_ms", "cache_read_input_tokens"}
    if order_by not in safe_order:
        order_by = "cost_usd"

    conditions = ["pe.enrich_status = 'embedded'"]
    params: list = []
    if advertiser_id:
        conditions.append("pe.advertiser_id = %s")
        params.append(advertiser_id)
    if q:
        conditions.append("rp.product_name ILIKE %s")
        params.append(f"%{q}%")

    where = "WHERE " + " AND ".join(conditions)

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM product_enriched pe JOIN rv_products rp ON rp.id = pe.rv_product_id {where}", params)
        total = cur.fetchone()["count"]

        cur.execute(f"""
            SELECT
                pe.rv_product_id,
                rp.product_name,
                rp.product_code,
                rp.image_url,
                pe.category,
                pe.model_used,
                pe.input_tokens,
                pe.output_tokens,
                pe.cache_read_input_tokens,
                pe.cache_creation_input_tokens,
                pe.cost_usd,
                pe.duration_ms,
                pe.enriched_at
              FROM product_enriched pe
              JOIN rv_products rp ON rp.id = pe.rv_product_id
             {where}
             ORDER BY pe.{order_by} DESC NULLS LAST
             LIMIT %s OFFSET %s
        """, params + [size, page * size])
        items = cur.fetchall()

    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/count")
def get_count():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE enrich_status = 'embedded') AS embedded
            FROM product_enriched
        """)
        return cur.fetchone()


@router.get("")
def list_products(
    q: Optional[str] = Query(None, description="상품명 LIKE 검색"),
    code: Optional[str] = Query(None, description="상품코드 LIKE 검색"),
    tag: Optional[str] = Query(None, description="태그 name 포함 검색"),
    category: Optional[str] = Query(None, description="카테고리 LIKE 검색"),
    status: Optional[str] = Query(None, description="enrich_status 필터 (예: embedded)"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
):
    conditions = []
    params: list = []

    if q:
        conditions.append("rp.product_name ILIKE %s")
        params.append(f"%{q}%")
    if code:
        conditions.append("rp.product_code ILIKE %s")
        params.append(f"%{code}%")
    if tag:
        conditions.append("pe.tags::text ILIKE %s")
        params.append(f"%{tag}%")
    if category:
        conditions.append("pe.category ILIKE %s")
        params.append(f"%{category}%")
    if status:
        conditions.append("pe.enrich_status = %s")
        params.append(status)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = f"""
        SELECT COUNT(*)
          FROM product_enriched pe
          JOIN rv_products rp ON rp.id = pe.rv_product_id
         {where}
    """
    list_sql = f"""
        SELECT
            pe.rv_product_id,
            rp.product_name,
            rp.product_code,
            rp.price,
            rp.sale_price,
            rp.image_url,
            rp.product_url,
            pe.category,
            pe.brand,
            pe.enrich_status,
            pe.tags,
            pe.desc_situation,
            pe.desc_style,
            pe.desc_persona,
            pe.enriched_at
          FROM product_enriched pe
          JOIN rv_products rp ON rp.id = pe.rv_product_id
         {where}
         ORDER BY pe.rv_product_id DESC
         LIMIT %s OFFSET %s
    """

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(count_sql, params)
        total = cur.fetchone()["count"]

        cur.execute(list_sql, params + [size, page * size])
        items = cur.fetchall()

    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/{rv_id}")
def get_product(rv_id: int):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                pe.*,
                rp.product_name,
                rp.product_code,
                rp.price        AS orig_price,
                rp.sale_price   AS orig_sale_price,
                rp.image_url,
                rp.product_url,
                rp.image_dir,
                rp.image_local_count,
                rp.scrape_status
            FROM product_enriched pe
            JOIN rv_products rp ON rp.id = pe.rv_product_id
            WHERE pe.rv_product_id = %s
        """, (rv_id,))
        row = cur.fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="not found")

        # 이미지 목록 (로컬 파일)
        image_dir = row.get("image_dir")
        images: list[str] = []
        if image_dir:
            from pathlib import Path
            d = Path(image_dir)
            if d.exists():
                images = sorted(
                    str(p) for p in d.iterdir()
                    if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
                )

        # 원본 이미지 URL (rv_product_images) — index 순으로 정렬
        cur.execute("""
            SELECT local_filename, src_url
              FROM rv_product_images
             WHERE rv_product_id = %s
             ORDER BY local_filename
        """, (rv_id,))
        img_rows = cur.fetchall()
        src_url_by_fname = {r["local_filename"]: r["src_url"] for r in img_rows}
        # images 리스트의 순서(파일명 정렬)와 동일하게 src_url 배열 구성
        image_src_urls: list[str] = []
        for p in images:
            fname = Path(p).name
            image_src_urls.append(src_url_by_fname.get(fname, ""))

        # product_descriptions (벡터 임베딩 텍스트)
        cur.execute("""
            SELECT perspective, description
              FROM product_descriptions
             WHERE rv_product_id = %s
             ORDER BY perspective
        """, (rv_id,))
        descriptions = cur.fetchall()

        return {**row, "images": images, "image_src_urls": image_src_urls, "descriptions": descriptions}


@router.get("/{rv_id}/image/{idx}")
def get_product_image(rv_id: int, idx: int):
    """로컬에 저장된 크롤링 이미지를 서빙합니다."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT image_dir FROM rv_products WHERE id = %s", (rv_id,))
        row = cur.fetchone()
    if not row or not row["image_dir"]:
        raise HTTPException(status_code=404, detail="image_dir not found")

    d = Path(row["image_dir"])
    if not d.exists():
        raise HTTPException(status_code=404, detail="image dir not found on disk")

    exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    files = sorted(p for p in d.iterdir() if p.suffix.lower() in exts)
    if idx < 0 or idx >= len(files):
        raise HTTPException(status_code=404, detail="image index out of range")

    return FileResponse(str(files[idx]))


# ─────────────────────────────────────────────────────────────
# 검색 로그
# ─────────────────────────────────────────────────────────────
logs_router = APIRouter(prefix="/search-logs", tags=["logs"])


@logs_router.get("")
def list_search_logs(
    zero_only: bool = Query(False, description="결과 0건인 로그만 조회"),
    llm_only: bool = Query(False, description="LLM 사용 로그만 조회"),
    q: Optional[str] = Query(None, description="query 검색"),
    advertiser_id: Optional[int] = Query(None, description="광고주 필터"),
    page: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=200),
):
    conditions = []
    params: list = []
    if zero_only:
        conditions.append("result_count = 0")
    if llm_only:
        conditions.append("llm_used = TRUE")
    if q:
        conditions.append("query ILIKE %s")
        params.append(f"%{q}%")
    if advertiser_id is not None:
        conditions.append("advertiser_id = %s")
        params.append(advertiser_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM search_logs {where}", params)
        total = cur.fetchone()["count"]

        cur.execute(
            f"""SELECT id, query, semantic_query, llm_used, llm_provider, backend,
                       advertiser_id, result_count, keyword_filters,
                       keyword_filter_relaxed, created_at,
                       llm_duration_ms, embed_duration_ms, total_duration_ms,
                       llm_input_tokens, llm_output_tokens, llm_cost_usd,
                       llm_parsed_json
                  FROM search_logs
                  {where}
                 ORDER BY created_at DESC
                 LIMIT %s OFFSET %s""",
            params + [size, page * size],
        )
        items = cur.fetchall()

    return {"total": total, "page": page, "size": size, "items": items}


@logs_router.get("/stats")
def search_log_stats():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE result_count = 0) AS zero_results,
                COUNT(*) FILTER (WHERE llm_used) AS llm_used,
                ROUND(AVG(result_count), 1) AS avg_results,
                COUNT(DISTINCT DATE(created_at)) AS active_days,
                COALESCE(SUM(llm_input_tokens), 0)  AS total_input_tokens,
                COALESCE(SUM(llm_output_tokens), 0) AS total_output_tokens,
                COALESCE(SUM(llm_cost_usd), 0)      AS total_cost_usd,
                COALESCE(AVG(llm_cost_usd) FILTER (WHERE llm_cost_usd IS NOT NULL), 0) AS avg_cost_usd
            FROM search_logs
        """)
        stats = cur.fetchone()

        cur.execute("""
            SELECT query, COUNT(*) AS cnt
              FROM search_logs
             WHERE result_count = 0
             GROUP BY query
             ORDER BY cnt DESC
             LIMIT 20
        """)
        top_zero = cur.fetchall()

    return {**stats, "top_zero_queries": top_zero}


@logs_router.get("/{log_id}/results")
def get_log_results(log_id: int):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT rank, rv_product_id, product_name, product_code,
                   image_url, product_url, price, sale_price,
                   category, brand, distance, perspective, description,
                   tags, desc_persona,
                   dense_dist, sparse_ip, morph_ip, fusion_score AS score,
                   rank_dense, rank_sparse, rank_morph
              FROM search_log_results
             WHERE log_id = %s
             ORDER BY rank
        """, (log_id,))
        items = cur.fetchall()

        # 벡터 공간 3D 좌표 재계산 — 라이브 검색과 동일하게 추천사유 팝업에 그래프를 띄우려면
        # 필요. search_log_results엔 좌표가 저장되지 않으므로, 저장된 (rv_product_id,
        # perspective)로 product_descriptions에서 임베딩을 다시 읽어 투영한다.
        # _project_for_viz는 질문을 원점에 두고 각도=상품 PCA·반경=distance만 쓰므로
        # 질문 재임베딩은 필요 없다 (저장된 distance 그대로 사용).
        query_coord3d = None
        keyed = [(it["rv_product_id"], it["perspective"]) for it in items
                 if it.get("rv_product_id") is not None and it.get("perspective") is not None]
        if keyed:
            cur.execute("""
                SELECT rv_product_id, perspective, embedding_bge::text AS emb
                  FROM product_descriptions
                 WHERE (rv_product_id, perspective) IN (
                       SELECT unnest(%s::int[]), unnest(%s::text[]))
            """, ([k[0] for k in keyed], [k[1] for k in keyed]))
            emb_map = {(r["rv_product_id"], r["perspective"]): r["emb"] for r in cur.fetchall()}

            embs, idxs, dists = [], [], []
            for i, it in enumerate(items):
                e = emb_map.get((it.get("rv_product_id"), it.get("perspective")))
                if e is not None and it.get("distance") is not None:
                    embs.append(json.loads(e)); idxs.append(i); dists.append(float(it["distance"]))
            if embs:
                try:
                    from .search_rv import _project_for_viz
                    query_coord3d, coords = _project_for_viz(None, embs, dists)
                    for j, i in enumerate(idxs):
                        items[i]["coord3d"] = coords[j]
                except Exception as ex:
                    import sys
                    print(f"[log results pca ERROR] {ex}", file=sys.stderr)

    return {"log_id": log_id, "items": items, "query_coord3d": query_coord3d}
