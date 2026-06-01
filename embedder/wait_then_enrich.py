"""
스크랩 배치 완료 대기 → LLM Enrich 자동 실행.

사용:
  python -m embedder.wait_then_enrich --advertiser-id 2 --enrich-limit 1000

동작:
  1. 백엔드 /batch-fetch/status 폴링 → status != RUNNING 이 될 때까지 대기
  2. batch_enrich_resilient 실행 (429 자동 대기·재개 내장)
"""

import argparse
import sys
import time
from datetime import datetime

import requests

BACKEND = "http://localhost:8090"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def wait_for_scrape(advertiser_id: int, poll_sec: int) -> None:
    url = f"{BACKEND}/advertisers/{advertiser_id}/products/batch-fetch/status"
    _log(f"스크랩 배치 완료 대기 시작 (advertiser={advertiser_id}, 폴링 {poll_sec}초)")

    while True:
        try:
            r = requests.get(url, timeout=10)
            if r.ok:
                d = r.json()
                status = d.get("status", "")
                total  = d.get("total", 0)
                done   = d.get("done", 0)
                ok     = d.get("ok", 0)
                fail   = d.get("fail", 0)

                if status == "RUNNING":
                    pct = f"{done/total*100:.1f}%" if total else "?"
                    _log(f"스크랩 진행 중... {done}/{total} ({pct})  OK={ok}  FAIL={fail}")
                else:
                    _log(f"스크랩 배치 종료: status={status or '미실행'}  OK={ok}  FAIL={fail}  TOTAL={total}")
                    return
            else:
                _log(f"상태 API HTTP {r.status_code} → 배치 미실행으로 간주, enrich 진행")
                return
        except Exception as e:
            _log(f"연결 오류: {e} → {poll_sec}초 후 재시도")

        time.sleep(poll_sec)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--advertiser-id", type=int, required=True,
                    help="스크랩 배치 완료를 기다릴 광고주 ID")
    ap.add_argument("--enrich-limit", type=int, default=1000,
                    help="enrich 처리 상품 수 (기본 1000)")
    ap.add_argument("--workers",      type=int, default=2)
    ap.add_argument("--image-limit",  type=int, default=30)
    ap.add_argument("--model",        default="haiku")
    ap.add_argument("--embed-backend", default="bge", choices=["bge", "openai", "both"])
    ap.add_argument("--poll-sec",     type=int, default=30,
                    help="스크랩 상태 폴링 주기(초)")
    args = ap.parse_args()

    wait_for_scrape(args.advertiser_id, args.poll_sec)

    _log(f"Enrich 시작: limit={args.enrich_limit}  workers={args.workers}  "
         f"model={args.model}  embed={args.embed_backend}")

    # batch_enrich_resilient.main() 직접 호출 (429 자동 대기 내장)
    sys.argv = [
        "batch_enrich_resilient",
        "--workers",      str(args.workers),
        "--image-limit",  str(args.image_limit),
        "--model",        args.model,
        "--embed-backend", args.embed_backend,
        "--limit",        str(args.enrich_limit),
    ]
    from embedder.batch_enrich_resilient import main as enrich_main
    return enrich_main()


if __name__ == "__main__":
    sys.exit(main())
