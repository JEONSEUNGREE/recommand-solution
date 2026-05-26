"""
인트로/배너 이미지 오염 픽스.

1) advertiser_image_blocks에 /intro/ 패턴 추가 (luvreparis 등 해당 광고주 대상)
2) 해당 광고주 enriched 상품들 재-enrich 실행

사용:
  # dry-run: 대상 광고주·상품 목록만 출력
  python -m embedder.fix_intro_images --dry-run

  # 특정 광고주만 (URL substring 매칭)
  python -m embedder.fix_intro_images --advertiser-url luvreparis --dry-run

  # 실제 실행 (재-enrich 포함)
  python -m embedder.fix_intro_images --advertiser-url luvreparis
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

from embedder.enrich_claude import DB_DSN
from embedder.embed_descriptions import embed_one
from embedder.enrich_claude import run_enrich_one

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 차단 패턴 목록 (모든 광고주에 URL 패턴 필터가 없으면 scrape.py에서만 막힘)
INTRO_PATTERNS = ["/intro/", "/banner/", "/event/", "/popup/", "/promotion/"]


def _log(msg: str) -> None:
    try:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)
    except Exception:
        pass


def find_advertisers(conn, url_substr: str | None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        if url_substr:
            cur.execute(
                "SELECT id, name, shop_url FROM advertisers WHERE shop_url ILIKE %s ORDER BY id",
                (f"%{url_substr}%",),
            )
        else:
            cur.execute("SELECT id, name, shop_url FROM advertisers ORDER BY id")
        return cur.fetchall()


def add_block_patterns(conn, advertiser_id: int, patterns: list[str], dry_run: bool) -> int:
    """주어진 패턴 중 아직 없는 것만 추가. 추가된 수 반환."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pattern FROM advertiser_image_blocks WHERE advertiser_id=%s",
            (advertiser_id,),
        )
        existing = {r[0] for r in cur.fetchall()}
    to_add = [p for p in patterns if p not in existing]
    if not to_add:
        return 0
    if dry_run:
        _log(f"  DRY: would add {len(to_add)} block patterns: {to_add}")
        return len(to_add)
    with conn.cursor() as cur:
        for pat in to_add:
            cur.execute(
                "INSERT INTO advertiser_image_blocks (advertiser_id, pattern, enabled, notes) VALUES (%s, %s, true, %s)",
                (advertiser_id, pat, "intro/banner image contamination fix"),
            )
    conn.commit()
    _log(f"  added {len(to_add)} block patterns: {to_add}")
    return len(to_add)


def find_enriched_products(conn, advertiser_id: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.id
                 FROM rv_products p
                 JOIN product_enriched pe ON pe.rv_product_id = p.id
                WHERE p.advertiser_id = %s
                ORDER BY p.id""",
            (advertiser_id,),
        )
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="실제 변경 없이 대상만 출력")
    ap.add_argument("--advertiser-url", default=None, help="광고주 shop_url substring (예: luvreparis)")
    ap.add_argument("--limit", type=int, default=None, help="재-enrich 상품 수 제한")
    ap.add_argument("--no-reenrich", action="store_true", help="패턴 추가만 하고 재-enrich 생략")
    args = ap.parse_args()

    conn = psycopg.connect(DB_DSN)

    advertisers = find_advertisers(conn, args.advertiser_url)
    if not advertisers:
        _log("광고주를 찾을 수 없습니다.")
        conn.close()
        return 1

    for adv in advertisers:
        _log(f"광고주: id={adv['id']} name={adv['name']} url={adv['shop_url']}")
        added = add_block_patterns(conn, adv["id"], INTRO_PATTERNS, args.dry_run)

        if args.no_reenrich:
            continue

        ids = find_enriched_products(conn, adv["id"])
        if args.limit:
            ids = ids[:args.limit]
        _log(f"  재-enrich 대상: {len(ids)}건")

        if args.dry_run:
            _log(f"  DRY: would re-enrich {ids[:10]}{'...' if len(ids)>10 else ''}")
            continue

        ok = fail = 0
        for rv_id in ids:
            try:
                run_enrich_one(rv_id, model="haiku", image_limit=30, save_db=True, conn=None)
                embed_one(conn, rv_id, "bge")
                ok += 1
                _log(f"  OK rv_id={rv_id}  ({ok+fail}/{len(ids)})")
            except Exception as e:
                fail += 1
                _log(f"  FAIL rv_id={rv_id}: {str(e)[:120]}")

        _log(f"  완료 OK={ok} FAIL={fail}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
