"""형태소 sparse PoC — 100 상품 × 5 쿼리 효과 확인.

읽기 전용. DB·기존 벡터 안 건드림.
"""
from __future__ import annotations

import pyarrow  # noqa  (load-order segfault 보호)
import os, sys, time
import psycopg

sys.path.insert(0, os.path.dirname(__file__))
from morpheme import (
    make_kiwi, build_vocab, compute_idf, encode_tfidf,
    inner_product, explain_match, extract_morphemes,
)

DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")
ADV = int(os.environ.get("ADV", "1"))
N_PRODUCTS = int(os.environ.get("N", "100"))

QUERIES = [
    "여름 데이트 옷",
    "로즈골드 14k 귀걸이",
    "캐주얼한 여성 가방",
    "프리미엄 화장품",
    "겨울 패딩",
    "선물용 목걸이",
]


def main() -> int:
    print(f"[PoC] 광고주 {ADV} · {N_PRODUCTS}상품 형태소 sparse 매칭\n")

    # 1) 데이터 로드
    conn = psycopg.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        """
        WITH picked AS (
          SELECT DISTINCT rv_product_id FROM product_descriptions
           WHERE advertiser_id=%s
              AND coalesce(trim(description),'') <> ''
           ORDER BY rv_product_id LIMIT %s
        )
        SELECT pd.id, pd.rv_product_id, pd.perspective, pd.description, rp.product_name
          FROM product_descriptions pd
          JOIN rv_products rp ON rp.id = pd.rv_product_id
         WHERE pd.rv_product_id IN (SELECT rv_product_id FROM picked)
         ORDER BY pd.rv_product_id, pd.perspective
        """,
        (ADV, N_PRODUCTS),
    )
    rows = cur.fetchall()
    print(f"로드: {N_PRODUCTS}상품 · {len(rows)}행 (4관점 × {len(rows)//4}상품)")
    conn.close()

    # 2) Kiwi + vocab
    print("\n[Kiwi 로딩...]")
    t0 = time.time()
    kiwi = make_kiwi()
    print(f"  Kiwi 준비 {time.time()-t0:.2f}s")

    docs = [r[3] for r in rows]
    t0 = time.time()
    vocab, df = build_vocab(docs, kiwi, min_df=2)
    idf = compute_idf(df, len(docs))
    vocab_inv = {v: k for k, v in vocab.items()}
    print(f"  vocab 구축 {time.time()-t0:.2f}s — 단어 {len(vocab)}개 (min_df=2)")

    # 흔한 단어 / 희귀 단어 일부 표시
    top_common = sorted(df.items(), key=lambda x: -x[1])[:15]
    print(f"  가장 흔한 단어: {[(m, c) for m, c in top_common]}")

    # 3) 모든 행 인코딩
    t0 = time.time()
    encoded = []  # [(rv_id, perspective, name, sparse_dict, raw_morphemes)]
    for r in rows:
        sp = encode_tfidf(r[3], vocab, idf, kiwi)
        encoded.append((r[1], r[2], r[4], sp, None))
    elapsed = time.time() - t0
    print(f"  본문 인코딩 {elapsed:.2f}s ({len(rows)/max(elapsed,1e-9):.0f}행/s, "
          f"평균 비0 토큰 {sum(len(e[3]) for e in encoded)//max(len(encoded),1)})")

    # 4) 쿼리 테스트
    for q in QUERIES:
        print(f"\n{'─'*72}\n[쿼리] {q}\n{'─'*72}")
        # 쿼리도 같은 추출 확인
        q_morphs = extract_morphemes(q, kiwi)
        q_sp = encode_tfidf(q, vocab, idf, kiwi)
        q_in_vocab = [vocab_inv[k] for k in q_sp]
        oov = [m for m in q_morphs if m not in vocab]
        print(f"  추출 형태소: {q_morphs}")
        print(f"  vocab 매칭:   {q_in_vocab}   {f'(미등재: {oov})' if oov else ''}")
        if not q_sp:
            print("  ⚠ vocab 매칭 0 — 결과 없음")
            continue

        # per-product 최고 점수 (4관점 중 max)
        best_per_prod = {}
        for rv_id, persp, name, doc_sp, _ in encoded:
            score = inner_product(q_sp, doc_sp)
            if score <= 0:
                continue
            cur = best_per_prod.get(rv_id)
            if cur is None or score > cur[0]:
                ms = explain_match(q_sp, doc_sp, vocab_inv)
                best_per_prod[rv_id] = (score, name, persp, ms)
        if not best_per_prod:
            print("  매칭 상품 0개")
            continue
        top = sorted(best_per_prod.items(), key=lambda x: -x[1][0])[:5]
        print(f"\n  Top {len(top)} (전체 {len(best_per_prod)}개 매칭):")
        for rv_id, (sc, name, persp, ms) in top:
            print(f"    rv={rv_id:5} [{persp:9}] {name[:34]:34}  score={sc:.4f}")
            for w, qw, dw, c in ms[:4]:
                print(f"        {w:10}  q={qw:.3f}  d={dw:.3f}  기여={c:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
