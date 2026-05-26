"""Stage 2 LLM Enrich — HTTP 어댑터.

Spring Boot 백엔드가 호출하는 단일 상품 enrich 엔드포인트.
실제 로직은 embedder.enrich_claude.run_enrich_one() 재사용.
"""
from __future__ import annotations

import traceback
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import psycopg

from .enrich_claude import DB_DSN, run_enrich_one

router = APIRouter(prefix="/enrich-llm")


@router.get("/common-images/{advertiser_id}")
def common_images(advertiser_id: int, min_products: int = 3, limit: int = 300):
    """광고주 내 여러 상품에 공통 등장하는 이미지 URL 목록 (배너·광고 후보)."""
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT src_url, COUNT(DISTINCT product_code)::int AS product_count
                FROM rv_product_images
                WHERE advertiser_id = %s
                GROUP BY src_url
                HAVING COUNT(DISTINCT product_code) >= %s
                ORDER BY product_count DESC
                LIMIT %s
                """,
                (advertiser_id, min_products, limit),
            )
            return [{"src_url": r[0], "product_count": r[1]} for r in cur.fetchall()]


@router.get("/image-blocks/{advertiser_id}")
def get_image_blocks(advertiser_id: int):
    """광고주의 차단 패턴 목록."""
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, pattern, enabled, notes FROM advertiser_image_blocks WHERE advertiser_id=%s ORDER BY id DESC",
                (advertiser_id,),
            )
            return [{"id": r[0], "pattern": r[1], "enabled": r[2], "notes": r[3]} for r in cur.fetchall()]


@router.delete("/image-blocks/{advertiser_id}/{block_id}")
def delete_image_block(advertiser_id: int, block_id: int):
    """차단 패턴 삭제."""
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM advertiser_image_blocks WHERE id=%s AND advertiser_id=%s", (block_id, advertiser_id))
    return {"deleted": block_id}


class ImageBlockBatchReq(BaseModel):
    patterns: list[str]
    notes: str = "공통 이미지 자동 차단"


@router.post("/image-blocks/{advertiser_id}/batch")
def add_image_blocks_batch(advertiser_id: int, req: ImageBlockBatchReq):
    """여러 URL 패턴을 advertiser_image_blocks에 한 번에 추가 (upsert)."""
    patterns = [p.strip() for p in req.patterns if p and p.strip()]
    if not patterns:
        return {"added": 0}
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO advertiser_image_blocks (advertiser_id, pattern, enabled, notes)
                VALUES (%s, %s, true, %s)
                ON CONFLICT (advertiser_id, pattern) DO UPDATE SET enabled = true
                """,
                [(advertiser_id, p, req.notes) for p in patterns],
            )
    return {"added": len(patterns)}


@router.post("/image-blocks/{advertiser_id}")
def add_image_block(advertiser_id: int, req: ImageBlockBatchReq):
    """단건 URL 패턴 추가."""
    return add_image_blocks_batch(advertiser_id, req)


@router.get("/image-products/{advertiser_id}")
def image_products(advertiser_id: int, src_url: str, limit: int = 5):
    """특정 이미지 URL을 사용하는 상품 목록 (최대 limit개)."""
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rpi.product_code, rp.product_name, rp.product_url
                FROM rv_product_images rpi
                JOIN rv_products rp
                  ON rp.advertiser_id = rpi.advertiser_id
                 AND rp.product_code  = rpi.product_code
                WHERE rpi.advertiser_id = %s
                  AND rpi.src_url = %s
                LIMIT %s
                """,
                (advertiser_id, src_url, limit),
            )
            return [
                {"product_code": r[0], "product_name": r[1], "product_url": r[2]}
                for r in cur.fetchall()
            ]


class EnrichRunReq(BaseModel):
    rv_product_id: int
    model: Literal["haiku", "sonnet", "opus"] = "haiku"
    image_limit: int | None = Field(default=30, ge=1, le=100)
    save_db: bool = True
    # 벡터까지 한 번에 — true면 LLM enrich 후 product_descriptions 임베딩까지 자동 적재
    also_embed: bool = True
    embed_backend: Literal["bge", "openai", "both"] = "bge"
    # 이미 enrich(extracted/validated)된 상품이면 LLM 호출 스킵하고 embed만
    skip_enrich_if_done: bool = True


@router.post("/run")
def run(req: EnrichRunReq):
    """단일 상품 enrich + 옵션으로 embed. 동기 호출."""
    try:
        import psycopg as _psycopg
        from .enrich_claude import DB_DSN as _DSN

        # 이미 enriched 된 상품이면 LLM 호출 스킵
        skip_llm = False
        if req.skip_enrich_if_done:
            with _psycopg.connect(_DSN) as _c, _c.cursor() as cur:
                cur.execute("SELECT enrich_status FROM product_enriched WHERE rv_product_id=%s", (req.rv_product_id,))
                row = cur.fetchone()
                if row and row[0] in ("extracted", "validated", "refined", "embedded"):
                    skip_llm = True

        result_summary = {"ok": True, "rv_product_id": req.rv_product_id, "llm_called": not skip_llm}

        if not skip_llm:
            result = run_enrich_one(
                req.rv_product_id,
                model=req.model,
                image_limit=req.image_limit,
                save_db=req.save_db,
            )
            result_summary.update({
                "product_code": result["product_code"],
                "n_images": result["n_images"],
                "model": req.model,
                "category": (result["data"].get("category") or {}).get("name"),
                "n_tags": len(result["data"].get("tags") or []),
                "n_ignored": len(result["data"].get("ignored_observations") or []),
            })

        if req.also_embed:
            from .embed_descriptions import embed_one as _embed_one
            with _psycopg.connect(_DSN) as conn:
                er = _embed_one(conn, req.rv_product_id, req.embed_backend)
            result_summary.update({
                "embedded": er.get("embedded"),
                "embed_backend": req.embed_backend,
            })

        return result_summary
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
