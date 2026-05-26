"""product_descriptions.embedding_sparse 백필 — 격리 환경 1회성 작업.

기존 dense 와 동일한 입력을 쓰기 위해, product_descriptions 의 기존
`description` 텍스트를 그대로 읽어 sparse 로 인코딩하고 embedding_sparse
컬럼만 UPDATE 한다. dense 컬럼(embedding_bge/embedding_openai)은 절대
건드리지 않는다.

사용:
  # 안전 확인: 아무것도 쓰지 않고 인코딩만 점검
  python backfill_sparse.py --dry-run --limit 5

  # 미백필 행 전체 채우기
  python backfill_sparse.py

  # 일부만
  python backfill_sparse.py --limit 200 --batch-size 32

기본 대상: embedding_sparse IS NULL 인 행만 (재실행 안전, 멱등).
"""
from __future__ import annotations

# ⚠️ 최상단 — pyarrow load-order segfault 회피
import pyarrow  # noqa: F401

import argparse
import os
import sys
import time

import psycopg

import sparse_model

DSN = os.getenv("DB_DSN", "postgresql://app:app@localhost:5433/recommend")


def fetch_targets(conn, limit: int | None, redo_all: bool) -> list[tuple[int, str]]:
    where = "coalesce(trim(description),'') <> ''"
    if not redo_all:
        where += " AND embedding_sparse IS NULL"
    sql = (
        f"SELECT id, description FROM product_descriptions "
        f"WHERE {where} ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as cur:
        cur.execute(sql)
        return [(r[0], r[1]) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="DB 에 쓰지 않고 인코딩만 — 안전 점검용")
    ap.add_argument("--redo-all", action="store_true",
                    help="이미 채워진 행도 다시 인코딩(기본은 NULL 만)")
    args = ap.parse_args()

    conn = psycopg.connect(DSN)
    targets = fetch_targets(conn, args.limit, args.redo_all)
    n = len(targets)
    print(f"[backfill] 대상 {n} 행 (dry_run={args.dry_run}, batch={args.batch_size})",
          file=sys.stderr)
    if n == 0:
        print("[backfill] 대상 없음 — 종료", file=sys.stderr)
        return 0

    # 모델 로드(첫 배치 전 한 번) 시간 측정
    t_load0 = time.time()
    sparse_model.get_model()
    print(f"[backfill] 모델 로드 {round(time.time() - t_load0, 1)}s", file=sys.stderr)

    done = 0
    t0 = time.time()
    for i in range(0, n, args.batch_size):
        chunk = targets[i:i + args.batch_size]
        ids = [c[0] for c in chunk]
        texts = [c[1] for c in chunk]
        literals = sparse_model.encode_sparsevec(texts, batch_size=args.batch_size)

        if not args.dry_run:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE product_descriptions SET embedding_sparse = %s::sparsevec "
                    "WHERE id = %s",
                    [(lit, _id) for lit, _id in zip(literals, ids)],
                )
            conn.commit()

        done += len(chunk)
        rate = done / max(time.time() - t0, 1e-6)
        eta_min = (n - done) / max(rate, 1e-6) / 60
        print(f"[backfill] {done}/{n}  rate={rate:.1f}/s  eta={eta_min:.1f}min "
              f"(예: id={ids[0]} nnz={literals[0].count(':')})", file=sys.stderr)

    elapsed = time.time() - t0
    print(f"[backfill] 완료 {done} 행, {round(elapsed, 1)}s "
          f"({done / max(elapsed, 1e-6):.1f}/s){' [DRY-RUN]' if args.dry_run else ''}",
          file=sys.stderr)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
