"""
사용량 한도(429) 자동 대기·재개형 enrich 배치.

벡터화되지 않은 모든 scraped 상품을 워커 N개로 enrich + embed 한다.
claude CLI 사용량 한도(429)에 걸리면 reset 시각까지 자동 대기 후 이어서 진행.
중단됐다 다시 실행해도 이미 끝난 상품은 건너뛴다 (DB enrich_status 기준).

사용:
  python -m embedder.batch_enrich_resilient --workers 2 --image-limit 30 --model haiku
  python -m embedder.batch_enrich_resilient --advertiser 4 --workers 2 --model haiku   # 피핀만
"""

import argparse
import queue
import sys
import threading
import time
from datetime import datetime

import psycopg

from embedder.enrich_claude import DB_DSN, RateLimitError, run_enrich_one
from embedder.embed_descriptions import embed_one

# Windows 기본 stdout 인코딩(cp949)은 em-dash 등 일부 문자를 못 써서
# 로그 출력이 UnicodeEncodeError 로 죽는다 → UTF-8 로 강제.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_pause_lock = threading.Lock()
_pause_until = 0.0  # epoch 초; 이 시각까지 모든 워커 대기


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
    """429 발생 시 호출 — reset 시각(+60초 버퍼)까지 전체 워커 일시정지."""
    global _pause_until
    target = (reset_at + 60) if reset_at else (time.time() + 3600)
    with _pause_lock:
        if target > _pause_until:
            _pause_until = target
    _log(f"사용량 한도 도달 — {datetime.fromtimestamp(target):%Y-%m-%d %H:%M:%S} 까지 대기")


_GARBAGE_NAME_PATTERNS = ("고객님", "결재창", "결제창")

def _is_garbage_name(name: str) -> bool:
    return any(p in name for p in _GARBAGE_NAME_PATTERNS)


def mark_low_quality(conn: "psycopg.Connection") -> int:
    """품질 기준 미달 상품을 product_enriched에 skip_low_quality로 등록.

    기준:
      1. display = 'N'  (미진열)
      2. 상품명에 '고객님' / '결재창' / '결제창' 포함
      3. 이미지 0개 AND 본문 30자 미만  (완전 껍데기)
    이미 처리(embedded / skip_low_quality)된 상품은 건드리지 않는다.
    """
    garbage_patterns = " OR ".join(
        f"rp.product_name ILIKE '%{p}%'" for p in _GARBAGE_NAME_PATTERNS
    )
    sql = f"""
        INSERT INTO product_enriched (rv_product_id, advertiser_id, product_code, enrich_status, enriched_at)
        SELECT rp.id, rp.advertiser_id, rp.product_code, 'skip_low_quality', NOW()
          FROM rv_products rp
          LEFT JOIN product_enriched pe ON pe.rv_product_id = rp.id
         WHERE rp.scrape_status = 'scraped'
           AND (pe.rv_product_id IS NULL OR pe.enrich_status NOT IN ('embedded', 'skip_low_quality'))
           AND (
                 rp.raw_payload->>'display' = 'N'
              OR ({garbage_patterns})
              OR (rp.image_local_count = 0 AND (rp.body_text IS NULL OR LENGTH(rp.body_text) < 30))
           )
        ON CONFLICT (rv_product_id) DO UPDATE
           SET enrich_status = 'skip_low_quality', enriched_at = NOW()
         WHERE product_enriched.enrich_status NOT IN ('embedded', 'skip_low_quality')
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.rowcount


def fetch_pending(limit: int | None = None, advertiser_id: int | None = None) -> list[int]:
    """품질 통과 & 아직 embedded 아닌 rv_product id 목록.

    skip_low_quality 마킹은 main() 에서 미리 호출한 mark_low_quality() 가 담당.
    advertiser_id 가 주어지면 해당 광고주만 필터링.

    NOTE: ILIKE 패턴을 SQL 리터럴로 박지 않고 파라미터로 넘긴다.
    SQL 안의 '%고객님%' 같은 한글 리터럴 + params 조합이면 psycopg 가
    '%' 를 placeholder 로 잘못 파싱하다가 한글 멀티바이트 중간에서 decode 실패.
    """
    params: list = [f"%{p}%" for p in _GARBAGE_NAME_PATTERNS]
    adv_cond = ""
    if advertiser_id is not None:
        adv_cond = "AND rp.advertiser_id = %s"
        params.append(advertiser_id)

    sql = f"""
        SELECT rp.id
          FROM rv_products rp
          LEFT JOIN product_enriched pe ON pe.rv_product_id = rp.id
         WHERE rp.scrape_status = 'scraped'
           AND rp.raw_payload->>'display' = 'Y'
           AND NOT (rp.image_local_count = 0 AND (rp.body_text IS NULL OR LENGTH(rp.body_text) < 30))
           AND rp.product_name NOT ILIKE %s
           AND rp.product_name NOT ILIKE %s
           AND rp.product_name NOT ILIKE %s
           AND (pe.rv_product_id IS NULL OR pe.enrich_status NOT IN ('embedded', 'skip_low_quality'))
           {adv_cond}
         ORDER BY rp.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def _worker(wid: int, q: "queue.Queue[int]", model: str, image_limit: int,
            embed_backend: str, stats: dict, permanent_fail: set) -> None:
    while True:
        try:
            rv_id = q.get_nowait()
        except queue.Empty:
            return
        # 상품마다 새 DB 커넥션 — 장시간 한도 대기 후 끊긴 커넥션 문제 회피.
        try:
            conn = psycopg.connect(DB_DSN)
            try:
                _wait_if_paused()
                run_enrich_one(rv_id, model=model, image_limit=image_limit,
                               save_db=True, conn=conn)
                embed_one(conn, rv_id, embed_backend)
            finally:
                conn.close()
            with stats["lock"]:
                stats["ok"] += 1
                done = stats["ok"] + stats["fail"]
            _log(f"[w{wid}] OK   rv_id={rv_id}  ({done}/{stats['total']})")
        except RateLimitError as e:
            # 한도 도달 — 같은 상품 큐로 반환 후 reset 시각까지 대기.
            try:
                _set_pause(e.reset_at)
            except Exception:
                pass
            q.put(rv_id)
            try:
                _wait_if_paused()
            except Exception:
                pass
        except Exception as e:
            with stats["lock"]:
                stats["fail"] += 1
                permanent_fail.add(rv_id)
                done = stats["ok"] + stats["fail"]
            _log(f"[w{wid}] FAIL rv_id={rv_id}: {str(e)[:180]}  ({done}/{stats['total']})")


def run_pass(ids: list[int], workers: int, model: str, image_limit: int,
             embed_backend: str, permanent_fail: set) -> dict:
    q: "queue.Queue[int]" = queue.Queue()
    for i in ids:
        q.put(i)
    stats = {"ok": 0, "fail": 0, "total": len(ids), "lock": threading.Lock()}
    threads = [
        threading.Thread(target=_worker, name=f"w{w}",
                         args=(w, q, model, image_limit, embed_backend, stats, permanent_fail))
        for w in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--image-limit", type=int, default=30)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--embed-backend", default="bge", choices=["bge", "openai", "both"])
    ap.add_argument("--limit", type=int, default=None, help="처리 상품 수 제한 (테스트용)")
    ap.add_argument("--advertiser", type=int, default=None, help="특정 광고주 ID만 처리 (예: 4=피핀)")
    args = ap.parse_args()

    permanent_fail: set = set()
    prev_remaining: int | None = None
    pass_no = 0

    with psycopg.connect(DB_DSN) as conn:
        marked = mark_low_quality(conn)
        conn.commit()
    if marked:
        _log(f"품질 미달 {marked}건 → skip_low_quality 마킹")

    while True:
        pending = [i for i in fetch_pending(args.limit, args.advertiser) if i not in permanent_fail]
        if not pending:
            _log("모든 상품 처리 완료.")
            break
        if prev_remaining is not None and len(pending) >= prev_remaining:
            _log(f"남은 {len(pending)}건이 더 줄지 않음 — 반복 실패로 종료.")
            break
        prev_remaining = len(pending)
        pass_no += 1
        _log(f"=== Pass {pass_no} 시작: {len(pending)}건 / workers={args.workers} "
             f"images<={args.image_limit} model={args.model} embed={args.embed_backend} ===")
        stats = run_pass(pending, args.workers, args.model, args.image_limit,
                         args.embed_backend, permanent_fail)
        _log(f"=== Pass {pass_no} 종료: OK={stats['ok']} FAIL={stats['fail']} ===")

    total_fail = len(permanent_fail)
    _log(f"배치 종료. 영구 실패 {total_fail}건"
         + (f": {sorted(permanent_fail)[:50]}" if total_fail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
