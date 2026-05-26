"""
rv_product_images 테이블 백필 스크립트.

기존 extracted.json 파일들에서 이미지 src URL을 읽어 DB에 기록한다.
새 스크랩부터는 enrich.py._record_image_srcs()가 자동으로 저장하므로 이 스크립트는 1회만 실행.

사용:
    python -m embedder.backfill_product_images [advertiser_id]

advertiser_id 생략 시 전체 광고주 처리.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import psycopg

DB_DSN = "postgresql://app:app@localhost:5433/recommend"
DATA_DIR = Path("D:/recommand-data")


def _safe_code_to_path(code: str) -> str:
    return re.sub(r"[^\w\-.]", "_", code)


def backfill(advertiser_id: int | None = None):
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            if advertiser_id:
                cur.execute(
                    "SELECT id, advertiser_id, product_code FROM rv_products WHERE advertiser_id=%s",
                    (advertiser_id,),
                )
            else:
                cur.execute("SELECT id, advertiser_id, product_code FROM rv_products")
            products = cur.fetchall()

        print(f"[backfill] {len(products)} products to scan")
        ok = skip = err = 0

        batch: list[tuple] = []

        for rv_id, adv_id, code in products:
            pdir = DATA_DIR / "advertisers" / str(adv_id) / "products" / _safe_code_to_path(code)
            extracted_path = pdir / "extracted.json"
            if not extracted_path.exists():
                skip += 1
                continue
            try:
                meta = json.loads(extracted_path.read_text(encoding="utf-8"))
                image_files = meta.get("image_files") or []
                for im in image_files:
                    src = im.get("src")
                    if not src:
                        continue
                    local = im.get("local_path") or ""
                    fname = Path(local).name or None
                    batch.append((adv_id, code, rv_id, src, fname))
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"  WARN {adv_id}/{code}: {e}")
                err += 1

            if len(batch) >= 500:
                _flush(conn, batch)
                batch.clear()

        if batch:
            _flush(conn, batch)

        print(f"[backfill] done ok={ok} skip={skip} err={err}")


def _flush(conn, rows: list[tuple]):
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rv_product_images
                (advertiser_id, product_code, rv_product_id, src_url, local_filename)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (advertiser_id, product_code, src_url) DO NOTHING
            """,
            rows,
        )
    conn.commit()
    print(f"  flushed {len(rows)} rows")


if __name__ == "__main__":
    adv_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    backfill(adv_id)
