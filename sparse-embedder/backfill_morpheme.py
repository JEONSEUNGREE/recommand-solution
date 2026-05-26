"""product_descriptions.embedding_morpheme 백필 — 1회성 (또는 NULL 행 채움).

morpheme_vocab 이 먼저 구축돼 있어야 함 (build_morpheme_vocab.py 실행).

기존 dense·sparse 컬럼은 절대 건드리지 않는다 — embedding_morpheme 만 UPDATE.
기본 대상: embedding_morpheme IS NULL 인 행 (재실행 안전).
"""
from __future__ import annotations

import pyarrow  # noqa
import argparse, os, sys, time
import psycopg

sys.path.insert(0, os.path.dirname(__file__))
from morpheme import (
    get_kiwi, encode_sparsevec, load_vocab_from_db, KIWI_MODEL_PATH,
)

DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")


def fetch_targets(conn, limit: int | None, redo_all: bool) -> list[tuple[int, str]]:
    where = "coalesce(trim(description),'') <> ''"
    if not redo_all:
        where += " AND embedding_morpheme IS NULL"
    sql = f"SELECT id, description FROM product_descriptions WHERE {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as cur:
        cur.execute(sql)
        return [(r[0], r[1]) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redo-all", action="store_true",
                    help="기존 embedding_morpheme 도 덮어씀 (기본은 NULL 만)")
    args = ap.parse_args()

    print(f"[backfill] DSN={DSN}  Kiwi={KIWI_MODEL_PATH}")
    conn = psycopg.connect(DSN)

    # 1) vocab 로드
    t0 = time.time()
    vocab, idf, _ = load_vocab_from_db(conn)
    if not vocab:
        print("[backfill] morpheme_vocab 이 비어 있음 — build_morpheme_vocab.py 먼저 실행")
        return 1
    print(f"[backfill] vocab {len(vocab)} 단어 로드 {time.time()-t0:.2f}s")

    # 2) Kiwi 준비
    t0 = time.time()
    get_kiwi()
    print(f"[backfill] Kiwi 준비 {time.time()-t0:.2f}s")

    # 3) 대상 로드
    targets = fetch_targets(conn, args.limit, args.redo_all)
    n = len(targets)
    print(f"[backfill] 대상 {n} 행 (dry_run={args.dry_run}, batch={args.batch_size})")
    if n == 0:
        print("[backfill] 대상 없음 — 종료")
        return 0

    # 4) 배치 인코딩 + UPDATE
    t0 = time.time()
    done = 0
    for i in range(0, n, args.batch_size):
        chunk = targets[i:i + args.batch_size]
        encoded = []
        for _id, desc in chunk:
            lit = encode_sparsevec(desc, vocab, idf)
            encoded.append((lit, _id))
        if not args.dry_run:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE product_descriptions SET embedding_morpheme = %s::sparsevec "
                    "WHERE id = %s",
                    encoded,
                )
            conn.commit()
        done += len(chunk)
        rate = done / max(time.time() - t0, 1e-6)
        eta_s = (n - done) / max(rate, 1e-6)
        print(f"[backfill] {done}/{n}  rate={rate:.0f}/s  eta={eta_s:.0f}s "
              f"(예: id={chunk[0][0]} nnz={encoded[0][0].count(':')})", file=sys.stderr)

    elapsed = time.time() - t0
    print(f"\n[backfill] 완료 {done} 행 {elapsed:.1f}s ({done/max(elapsed,1e-6):.0f}/s)"
          f"{' [DRY-RUN]' if args.dry_run else ''}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
