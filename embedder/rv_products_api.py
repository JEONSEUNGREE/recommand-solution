"""
rv_products 관리 API — Spring Boot 없이 Python embedder 단독 운영.

GET  /advertisers/{id}/products                    페이지네이션 + 상태 (scrape/enrich/embed)
POST /advertisers/{id}/products/{code}/scrape      단일 스크랩
POST /advertisers/{id}/products/{code}/enrich/llm  단일 LLM enrich + embed
POST /advertisers/{id}/products/batch-fetch        배치 스크랩 (백그라운드)
GET  /advertisers/{id}/products/batch-fetch/status
POST /advertisers/{id}/products/batch-fetch/cancel
POST /advertisers/{id}/products/batch-enrich       배치 enrich (백그라운드)
GET  /advertisers/{id}/products/batch-enrich/status
POST /advertisers/{id}/products/batch-enrich/cancel
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg.rows import dict_row

from .enrich import _process_html, DATA_DIR

DB_DSN = __import__("os").environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")

router = APIRouter(prefix="/advertisers")


# ─────────────────────────────────────────────────────────────
# 배치 진행 상태 (메모리 내, 재시작 시 초기화)
# ─────────────────────────────────────────────────────────────

@dataclass
class BatchProgress:
    status: str = "IDLE"   # IDLE / RUNNING / COMPLETED / FAILED / CANCELLED
    total: int = 0
    done: int = 0
    ok: int = 0
    fail: int = 0
    current_code: str = ""
    elapsed_ms: int = 0
    last_error: str = ""
    _cancel: bool = field(default=False, repr=False)
    _start: float = field(default=0.0, repr=False)


_scrape_progress: dict[int, BatchProgress] = {}
_enrich_progress: dict[int, BatchProgress] = {}


def _sp(adv_id: int) -> BatchProgress:
    if adv_id not in _scrape_progress:
        _scrape_progress[adv_id] = BatchProgress()
    return _scrape_progress[adv_id]


def _ep(adv_id: int) -> BatchProgress:
    if adv_id not in _enrich_progress:
        _enrich_progress[adv_id] = BatchProgress()
    return _enrich_progress[adv_id]


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

def _conn():
    return psycopg.connect(DB_DSN, row_factory=dict_row)


def _progress_dict(p: BatchProgress) -> dict:
    return {
        "status": p.status,
        "total": p.total,
        "done": p.done,
        "ok": p.ok,
        "fail": p.fail,
        "currentCode": p.current_code,
        "elapsedMs": p.elapsed_ms,
        "lastError": p.last_error,
    }


def _get_advertiser(conn, adv_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, shop_url, selector_detail, selector_name, selector_price, image_attrs "
            "FROM advertisers WHERE id = %s",
            (adv_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"advertiser {adv_id} not found")
    return row


# ─────────────────────────────────────────────────────────────
# 상품 목록
# ─────────────────────────────────────────────────────────────

@router.get("/{advertiser_id}/products")
def list_products(
    advertiser_id: int,
    page: int = 0,
    size: int = 30,
    filter: Optional[str] = None,
):
    """rv_products 목록 + scrape/enrich/embed 상태."""
    filter = (filter or "").strip()

    where = "WHERE rp.advertiser_id = %(adv)s"
    if filter == "to_scrape":
        where += " AND rp.scrape_status IS NULL"
    elif filter == "to_enrich":
        where += " AND rp.scrape_status = 'scraped' AND rp.enrich_method IS NULL"
    elif filter == "done":
        where += " AND rp.enrich_method IS NOT NULL"

    params = {"adv": advertiser_id, "lim": size, "off": page * size}

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM rv_products rp {where}", params)
        total = cur.fetchone()["count"]

        cur.execute(
            f"""SELECT rp.id, rp.product_code, rp.product_name,
                       rp.image_url, rp.product_url,
                       rp.scrape_status, rp.body_text_len,
                       rp.image_local_count, rp.enrich_method
                  FROM rv_products rp
                  {where}
                 ORDER BY rp.id ASC
                 LIMIT %(lim)s OFFSET %(off)s""",
            params,
        )
        rows = cur.fetchall()
        page_ids = [r["id"] for r in rows]

        # 현재 페이지 중 enriched / embedded 여부
        enriched_ids: list[int] = []
        embedded_ids: list[int] = []
        if page_ids:
            placeholders = ",".join(["%s"] * len(page_ids))
            cur.execute(
                f"SELECT rv_product_id FROM product_enriched WHERE rv_product_id IN ({placeholders})",
                page_ids,
            )
            enriched_ids = [r["rv_product_id"] for r in cur.fetchall()]

            cur.execute(
                f"SELECT rv_product_id FROM product_enriched "
                f"WHERE enrich_status='embedded' AND rv_product_id IN ({placeholders})",
                page_ids,
            )
            embedded_ids = [r["rv_product_id"] for r in cur.fetchall()]

        # 광고주 전체 집계
        cur.execute(
            """SELECT
                   COUNT(*)                                                       AS total,
                   COUNT(*) FILTER (WHERE scrape_status IS NULL)                 AS pending,
                   COUNT(*) FILTER (WHERE scrape_status = 'scraped'
                                    AND enrich_method IS NULL)                   AS scraped_pending_enrich,
                   COUNT(*) FILTER (WHERE enrich_method IS NOT NULL)             AS done
               FROM rv_products WHERE advertiser_id = %s""",
            (advertiser_id,),
        )
        c = cur.fetchone()

        cur.execute(
            "SELECT COUNT(*) FROM product_enriched WHERE advertiser_id = %s",
            (advertiser_id,),
        )
        llm_enriched = cur.fetchone()["count"]

        cur.execute(
            "SELECT COUNT(*) FROM product_enriched "
            "WHERE advertiser_id = %s AND enrich_status='embedded'",
            (advertiser_id,),
        )
        vectorized = cur.fetchone()["count"]

    # camelCase로 변환 (products.html renderSyncTable이 camelCase 기대)
    items = [
        {
            "id":              r["id"],
            "productCode":     r["product_code"],
            "productName":     r["product_name"],
            "imageUrl":        r["image_url"],
            "productUrl":      r["product_url"],
            "scrapeStatus":    r["scrape_status"],
            "bodyTextLen":     r["body_text_len"] or 0,
            "imageLocalCount": r["image_local_count"] or 0,
            "enrichMethod":    r["enrich_method"],
        }
        for r in rows
    ]

    return {
        "page": page,
        "size": size,
        "total": total,
        "filter": filter,
        "items": items,
        "enrichedIds": enriched_ids,
        "embeddedIds": embedded_ids,
        "counts": {
            "total":                  c["total"],
            "pending":                c["pending"],
            "scraped_pending_enrich": c["scraped_pending_enrich"],
            "done":                   c["done"],
            "llm_enriched":           llm_enriched,
            "vectorized":             vectorized,
        },
    }


# ─────────────────────────────────────────────────────────────
# 단일 스크랩
# ─────────────────────────────────────────────────────────────

def _do_scrape(advertiser_id: int, product_code: str) -> dict:
    import requests as req_lib
    from .scrape import UA

    with _conn() as conn:
        adv = _get_advertiser(conn, advertiser_id)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT product_url FROM rv_products "
                "WHERE advertiser_id=%s AND product_code=%s",
                (advertiser_id, product_code),
            )
            row = cur.fetchone()
        if not row or not row["product_url"]:
            raise HTTPException(404, "product not found or has no product_url")
        product_url = row["product_url"]

    try:
        resp = req_lib.get(product_url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        _update_scrape_status(advertiser_id, product_code, "failed", 0, 0, None, None, str(e))
        raise HTTPException(502, f"fetch failed: {e}")

    result = _process_html(
        advertiser_id, product_code, product_url, resp.text,
        max_images=30,
        selector_detail=adv.get("selector_detail"),
        selector_name=adv.get("selector_name"),
        selector_price=adv.get("selector_price"),
        image_attrs=adv.get("image_attrs"),
        detail_anchor_start=None,
        detail_anchor_end=None,
        image_url_blocks=[],
    )

    ok_images = [i for i in result.get("image_files", []) if "local_path" in i]
    _update_scrape_status(
        advertiser_id, product_code, "scraped",
        len(result.get("body_text", "")),
        len(ok_images),
        result.get("html_path"),
        result.get("image_dir"),
        None,
    )
    return result


def _update_scrape_status(adv_id, code, status, body_len, img_count, html_path, img_dir, error):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE rv_products
                  SET scrape_status=%s, body_text_len=%s, image_local_count=%s,
                      html_path=%s, image_dir=%s, scraped_at=NOW(), scrape_error=%s, mod_date=NOW()
                WHERE advertiser_id=%s AND product_code=%s""",
            (status, body_len, img_count, html_path, img_dir, error, adv_id, code),
        )


@router.post("/{advertiser_id}/products/{code}/scrape")
def scrape_one(advertiser_id: int, code: str):
    return _do_scrape(advertiser_id, code)


# ─────────────────────────────────────────────────────────────
# 단일 LLM Enrich
# ─────────────────────────────────────────────────────────────

@router.post("/{advertiser_id}/products/{code}/enrich/llm")
def enrich_llm_one(
    advertiser_id: int,
    code: str,
    model: str = "haiku",
    imageLimit: int = 10,
    force: bool = False,
):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM rv_products WHERE advertiser_id=%s AND product_code=%s",
            (advertiser_id, code),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "product not found")
    rv_id = row["id"]

    from .enrich_api import run as _run_api
    from .enrich_api import EnrichRunReq

    req = EnrichRunReq(
        rv_product_id=rv_id,
        model=model,
        image_limit=imageLimit,
        save_db=True,
        also_embed=True,
        embed_backend="bge",
        skip_enrich_if_done=(not force),
    )
    result = _run_api(req)

    # rv_products.enrich_method 업데이트 (필터 "done" 조건용)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE rv_products SET enrich_method='llm', enriched_at=NOW(), mod_date=NOW() "
            "WHERE advertiser_id=%s AND product_code=%s",
            (advertiser_id, code),
        )
    return result


# ─────────────────────────────────────────────────────────────
# 배치 스크랩
# ─────────────────────────────────────────────────────────────

def _batch_scrape_thread(adv_id: int, count: int, interval_ms: int, concurrency: int):
    p = _sp(adv_id)
    p.status = "RUNNING"
    p._start = time.time()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT product_code FROM rv_products "
                "WHERE advertiser_id=%s AND scrape_status IS NULL "
                "ORDER BY id ASC LIMIT %s",
                (adv_id, count),
            )
            codes = [r["product_code"] for r in cur.fetchall()]
        p.total = len(codes)

        import concurrent.futures
        def _scrape_one_safe(code):
            if p._cancel:
                return False
            p.current_code = code
            try:
                _do_scrape(adv_id, code)
                p.ok += 1
                return True
            except Exception as e:
                p.last_error = str(e)[:200]
                p.fail += 1
                return False
            finally:
                p.done += 1
                p.elapsed_ms = int((time.time() - p._start) * 1000)
                if interval_ms > 0:
                    time.sleep(interval_ms / 1000)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(_scrape_one_safe, codes))

        p.status = "CANCELLED" if p._cancel else "COMPLETED"
    except Exception as e:
        p.status = "FAILED"
        p.last_error = str(e)[:200]
    finally:
        p.elapsed_ms = int((time.time() - p._start) * 1000)
        p._cancel = False


@router.post("/{advertiser_id}/products/batch-fetch")
def batch_fetch(
    advertiser_id: int,
    count: int = 500,
    intervalMs: int = 1000,
    concurrency: int = 4,
    mode: str = "page",
    batchSize: int = 100,
):
    p = _sp(advertiser_id)
    if p.status == "RUNNING":
        return {"status": "already_running", "advertiserId": advertiser_id, "done": p.done, "total": p.total}
    p.__init__()
    t = threading.Thread(
        target=_batch_scrape_thread,
        args=(advertiser_id, count, intervalMs, concurrency),
        daemon=True,
        name=f"batch-scrape-{advertiser_id}",
    )
    t.start()
    return {"status": "started", "advertiserId": advertiser_id, "count": count}


@router.get("/{advertiser_id}/products/batch-fetch/status")
def batch_fetch_status(advertiser_id: int):
    return _progress_dict(_sp(advertiser_id))


@router.post("/{advertiser_id}/products/batch-fetch/cancel")
def batch_fetch_cancel(advertiser_id: int):
    _sp(advertiser_id)._cancel = True
    return {"status": "cancel_requested", "advertiserId": advertiser_id}


# ─────────────────────────────────────────────────────────────
# 배치 LLM Enrich
# ─────────────────────────────────────────────────────────────

def _batch_enrich_thread(adv_id: int, count: int, model: str, image_limit: int,
                          concurrency: int, interval_ms: int, embed_backend: str):
    p = _ep(adv_id)
    p.status = "RUNNING"
    p._start = time.time()
    try:
        with _conn() as conn, conn.cursor() as cur:
            # scrape됐지만 product_enriched에 없는 상품
            cur.execute(
                """SELECT rp.id, rp.product_code
                     FROM rv_products rp
                    WHERE rp.advertiser_id = %s
                      AND rp.scrape_status = 'scraped'
                      AND NOT EXISTS (
                          SELECT 1 FROM product_enriched pe
                           WHERE pe.rv_product_id = rp.id
                             AND pe.enrich_status = 'embedded'
                      )
                    ORDER BY rp.id ASC
                    LIMIT %s""",
                (adv_id, count),
            )
            products = [(r["id"], r["product_code"]) for r in cur.fetchall()]
        p.total = len(products)

        from .enrich_api import run as _run_api
        from .enrich_api import EnrichRunReq

        import concurrent.futures

        def _enrich_one(item):
            rv_id, code = item
            if p._cancel:
                return False
            p.current_code = code
            try:
                req = EnrichRunReq(
                    rv_product_id=rv_id,
                    model=model,
                    image_limit=image_limit,
                    save_db=True,
                    also_embed=True,
                    embed_backend=embed_backend,
                    skip_enrich_if_done=True,
                )
                _run_api(req)
                # rv_products.enrich_method 업데이트
                with _conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE rv_products SET enrich_method='llm', enriched_at=NOW(), mod_date=NOW() "
                        "WHERE id=%s",
                        (rv_id,),
                    )
                p.ok += 1
                return True
            except Exception as e:
                p.last_error = str(e)[:200]
                p.fail += 1
                return False
            finally:
                p.done += 1
                p.elapsed_ms = int((time.time() - p._start) * 1000)
                if interval_ms > 0:
                    time.sleep(interval_ms / 1000)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(_enrich_one, products))

        p.status = "CANCELLED" if p._cancel else "COMPLETED"
    except Exception as e:
        p.status = "FAILED"
        p.last_error = str(e)[:200]
    finally:
        p.elapsed_ms = int((time.time() - p._start) * 1000)
        p._cancel = False


@router.post("/{advertiser_id}/products/batch-enrich")
def batch_enrich(
    advertiser_id: int,
    count: int = 20,
    model: str = "haiku",
    imageLimit: int = 10,
    concurrency: int = 2,
    intervalMs: int = 0,
    embedBackend: str = "bge",
):
    p = _ep(advertiser_id)
    if p.status == "RUNNING":
        return {"status": "already_running", "advertiserId": advertiser_id, "done": p.done, "total": p.total}
    p.__init__()
    t = threading.Thread(
        target=_batch_enrich_thread,
        args=(advertiser_id, count, model, imageLimit, concurrency, intervalMs, embedBackend),
        daemon=True,
        name=f"batch-enrich-{advertiser_id}",
    )
    t.start()
    return {"status": "started", "advertiserId": advertiser_id, "count": count}


@router.get("/{advertiser_id}/products/batch-enrich/status")
def batch_enrich_status(advertiser_id: int):
    return _progress_dict(_ep(advertiser_id))


@router.post("/{advertiser_id}/products/batch-enrich/cancel")
def batch_enrich_cancel(advertiser_id: int):
    _ep(advertiser_id)._cancel = True
    return {"status": "cancel_requested", "advertiserId": advertiser_id}
