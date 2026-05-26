"""
연예인/셀럽 마케팅 문구 오염 상품 재처리.

product_enriched + product_descriptions 에서 연예인/셀럽 표현이 들어간
60여 개 상품만 골라:
  1) run_enrich_one  — 새 SYSTEM_PROMPT로 재분석 (이미지는 디스크 기존 것 사용)
  2) embed_one       — bge 벡터 재생성

사용:
  python -m embedder.reenrich_contaminated [--dry-run] [--limit N] [--workers N]
"""

import argparse
import queue
import sys
import threading
import time
from datetime import datetime

import psycopg
from dotenv import load_dotenv

load_dotenv()

from embedder.enrich_claude import DB_DSN, RateLimitError, run_enrich_one
from embedder.embed_descriptions import embed_one

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_CONTAMINATION_PATTERNS = [
    "%연예인%",
    "%셀럽%",
    "%셀러브리티%",
    "%아이돌%",
    "%스타가%",
    "%스타의%",
    "%협찬%",
    "%드라마 출연%",
    "%방송 출연%",
]

_pause_lock = threading.Lock()
_pause_until = 0.0


def _log(msg: str) -> None:
    try:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)
    except Exception:
        pass


def _wait_if_paused() -> None:
    while True:
        with _pause_lock:
            wait = _pause_until - time.time()
        if wait <= 0:
            return
        time.sleep(min(wait, 30))


def _set_pause(reset_at: float | None) -> None:
    global _pause_until
    target = (reset_at + 60) if reset_at else (time.time() + 3600)
    with _pause_lock:
        if target > _pause_until:
            _pause_until = target
    _log(f"사용량 한도 — {datetime.fromtimestamp(target):%H:%M:%S} 까지 대기")


def fetch_contaminated_ids(limit: int | None = None) -> list[int]:
    """product_enriched descriptions·tags 또는 product_descriptions.description에
    연예인/셀럽 패턴이 포함된 rv_product_id 목록."""
    pattern_sql = " OR ".join(
        [
            f"pe.desc_situation ILIKE '{p}' OR pe.desc_material ILIKE '{p}' "
            f"OR pe.desc_style ILIKE '{p}' OR pe.desc_persona ILIKE '{p}' "
            f"OR pe.tags::text ILIKE '{p}'"
            for p in _CONTAMINATION_PATTERNS
        ]
        + [
            f"pd.description ILIKE '{p}'"
            for p in _CONTAMINATION_PATTERNS
        ]
    )
    sql = f"""
        SELECT DISTINCT COALESCE(pe.rv_product_id, pd.rv_product_id) AS rv_id
          FROM product_enriched pe
          FULL JOIN product_descriptions pd ON pd.rv_product_id = pe.rv_product_id
         WHERE {pattern_sql}
         ORDER BY rv_id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]


def _worker(wid: int, q: "queue.Queue[int]", dry_run: bool,
            stats: dict, permanent_fail: set) -> None:
    while True:
        try:
            rv_id = q.get_nowait()
        except queue.Empty:
            return
        try:
            conn = psycopg.connect(DB_DSN)
            try:
                _wait_if_paused()
                if dry_run:
                    _log(f"[w{wid}] DRY  rv_id={rv_id}")
                    with stats["lock"]:
                        stats["ok"] += 1
                    continue

                run_enrich_one(rv_id, model="haiku", image_limit=30, save_db=True, conn=conn)
                embed_one(conn, rv_id, "bge")
            finally:
                conn.close()
            with stats["lock"]:
                stats["ok"] += 1
                done = stats["ok"] + stats["fail"]
            _log(f"[w{wid}] OK   rv_id={rv_id}  ({done}/{stats['total']})")
        except RateLimitError as e:
            try:
                _set_pause(e.reset_at)
            except Exception:
                pass
            q.put(rv_id)
            _wait_if_paused()
        except Exception as exc:
            with stats["lock"]:
                stats["fail"] += 1
                permanent_fail.add(rv_id)
                done = stats["ok"] + stats["fail"]
            _log(f"[w{wid}] FAIL rv_id={rv_id}: {str(exc)[:200]}  ({done}/{stats['total']})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="대상 목록만 출력, 실제 처리 안 함")
    ap.add_argument("--limit", type=int, default=None, help="처리 상품 수 제한")
    ap.add_argument("--workers", type=int, default=1, help=".claude.json 경합 방지 — 기본 1")
    args = ap.parse_args()

    _log("오염 상품 ID 조회 중...")
    ids = fetch_contaminated_ids(args.limit)
    _log(f"대상 {len(ids)}건: {ids[:20]}{'...' if len(ids) > 20 else ''}")

    if not ids:
        _log("오염 상품 없음 — 완료.")
        return 0

    if args.dry_run:
        _log("--dry-run: 실제 처리를 건너뜁니다.")

    q: "queue.Queue[int]" = queue.Queue()
    for i in ids:
        q.put(i)

    permanent_fail: set = set()
    stats = {"ok": 0, "fail": 0, "total": len(ids), "lock": threading.Lock()}
    threads = [
        threading.Thread(target=_worker, name=f"w{w}",
                         args=(w, q, args.dry_run, stats, permanent_fail))
        for w in range(args.workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    _log(f"완료 — OK={stats['ok']} FAIL={stats['fail']}")
    if permanent_fail:
        _log(f"영구 실패 {len(permanent_fail)}건: {sorted(permanent_fail)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
