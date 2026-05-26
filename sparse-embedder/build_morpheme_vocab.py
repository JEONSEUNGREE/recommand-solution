"""morpheme_vocab 테이블 구축 — 1회성 실행 (또는 vocab 재구축 시).

product_descriptions.description 전부 분석해서 형태소 vocab + IDF 저장.
재실행하면 기존 vocab 을 비우고 새로 만든다(TRUNCATE).
이후 backfill_morpheme.py 가 이 vocab 으로 embedding_morpheme 채움.
"""
from __future__ import annotations

import pyarrow  # noqa  (load-order segfault 보호)
import os, sys, time
import psycopg

sys.path.insert(0, os.path.dirname(__file__))
from morpheme import (
    get_kiwi, build_vocab, compute_idf, SPARSE_DIM, KIWI_MODEL_PATH,
)

DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")
MIN_DF = int(os.environ.get("MORPHEME_MIN_DF", "3"))


def main() -> int:
    print(f"[vocab] DSN={DSN}")
    print(f"[vocab] Kiwi model: {KIWI_MODEL_PATH}")
    print(f"[vocab] min_df={MIN_DF}, sparsevec dim={SPARSE_DIM}")

    conn = psycopg.connect(DSN)

    # 1) description 전체 로드
    with conn.cursor() as cur:
        cur.execute(
            "SELECT description FROM product_descriptions "
            "WHERE coalesce(trim(description),'') <> ''"
        )
        docs = [r[0] for r in cur.fetchall()]
    n = len(docs)
    print(f"[vocab] 분석 대상: {n} 문서")
    if n == 0:
        print("[vocab] description 0건 — 종료")
        return 1

    # 2) Kiwi 로딩
    t0 = time.time()
    get_kiwi()
    print(f"[vocab] Kiwi 준비 {time.time()-t0:.2f}s")

    # 3) vocab + IDF 구축
    t0 = time.time()
    vocab, df = build_vocab(docs, min_df=MIN_DF)
    idf = compute_idf(df, n)
    elapsed = time.time() - t0
    print(f"[vocab] 사전 구축 {elapsed:.1f}s — 단어 {len(vocab)}개 (min_df={MIN_DF})")

    if len(vocab) > SPARSE_DIM:
        print(f"[vocab] 경고: 단어수({len(vocab)}) > sparsevec 차원({SPARSE_DIM})")
        print("       migration 019 의 sparsevec(N) 차원을 늘려야 함. 중단.")
        return 2

    # 4) 가장 흔한/희귀 단어 샘플
    top_common = sorted(df.items(), key=lambda x: -x[1])[:15]
    top_rare = sorted(df.items(), key=lambda x: x[1])[:10]
    print(f"\n[vocab] 가장 흔한 15개: {top_common}")
    print(f"[vocab] 가장 희귀(min_df) 10개: {top_rare}")

    # 5) DB 적재 (TRUNCATE → INSERT)
    print(f"\n[vocab] DB 적재 시작...")
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE morpheme_vocab")
        rows = [
            (vocab[m], m, df[m], idf[m]) for m in vocab
        ]
        cur.executemany(
            "INSERT INTO morpheme_vocab (idx, morpheme, df, idf) VALUES (%s, %s, %s, %s)",
            rows,
        )
    conn.commit()
    print(f"[vocab] DB 적재 {time.time()-t0:.1f}s ({len(rows)} 행)")
    conn.close()
    print("[vocab] 완료 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
