"""product_descriptions.colbert_vecs 백필 스크립트.

migration 020이 컬럼을 추가한 뒤 이 스크립트로 4,224행을 채운다.
멱등: 이미 채워진 행은 건너뜀.

실행:
  cd colbert-reranker
  ../.venv-sparse/Scripts/python backfill.py
  (또는 python backfill.py --dsn postgresql://... --batch 8)
"""
from __future__ import annotations

import pyarrow  # noqa: F401

import argparse
import json
import sys
import time

import psycopg
import psycopg.rows

import model as colbert_model

DEFAULT_DSN = "postgresql://app:app@localhost:5433/recommend"


def run(dsn: str, batch_size: int, dry_run: bool) -> None:
    conn = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS n FROM product_descriptions WHERE colbert_vecs IS NULL")
    total = cur.fetchone()["n"]
    print(f"백필 대상: {total}행  (colbert_vecs IS NULL)", flush=True)

    if total == 0:
        print("모두 채워짐 — 완료.")
        return

    cur.execute("""
        SELECT id, description
          FROM product_descriptions
         WHERE colbert_vecs IS NULL
           AND description IS NOT NULL AND description != ''
         ORDER BY id
    """)
    rows = cur.fetchall()
    print(f"유효 행(description 있음): {len(rows)}", flush=True)

    done, skipped, t0 = 0, 0, time.time()

    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        texts = [r["description"] for r in batch]
        ids = [r["id"] for r in batch]

        try:
            vecs_list = colbert_model.encode_colbert(texts, batch_size=batch_size)
        except Exception as e:
            print(f"  [encode ERROR] ids={ids}: {e}", file=sys.stderr)
            skipped += len(batch)
            continue

        if dry_run:
            print(f"  [dry-run] id={ids[0]} tokens={len(vecs_list[0])} dim={len(vecs_list[0][0]) if vecs_list[0] else 0}")
            done += len(batch)
            continue

        with conn.cursor() as update_cur:
            for row_id, vecs in zip(ids, vecs_list):
                update_cur.execute(
                    "UPDATE product_descriptions SET colbert_vecs = %s WHERE id = %s",
                    (json.dumps(vecs), row_id),
                )
        conn.commit()
        done += len(batch)

        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(rows) - done) / rate if rate > 0 else 0
        print(
            f"  {done}/{len(rows)} ({done*100//len(rows)}%)  "
            f"{rate:.1f}행/s  ETA {eta/60:.1f}분",
            flush=True,
        )

    print(f"\n완료: done={done}  skipped={skipped}  total_time={time.time()-t0:.0f}s")

    # 검증
    cur.execute("""
        SELECT
          COUNT(*) AS total,
          COUNT(colbert_vecs) AS filled,
          COUNT(*) FILTER (WHERE colbert_vecs IS NULL) AS missing
        FROM product_descriptions
    """)
    v = cur.fetchone()
    print(f"검증: total={v['total']}  colbert_vecs={v['filled']}  미백필={v['missing']}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--batch", type=int, default=4,
                    help="배치 크기 (CPU라 작게 — 기본 4)")
    ap.add_argument("--dry-run", action="store_true",
                    help="DB 쓰기 없이 인코딩만 확인")
    args = ap.parse_args()
    run(args.dsn, args.batch, args.dry_run)
