"""LLM 없이 벡터 검색만 빠르게 검증하는 스모크 테스트.

사용 예:
  .venv/bin/python scripts/smoke_search.py "봄에 입을 산뜻한 원피스"
  .venv/bin/python scripts/smoke_search.py "남자 검정 청바지" --category 하의
"""
import argparse
import os
import sys

import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

DB_DSN = os.getenv("DB_DSN", "postgresql://app:app@localhost:5433/recommend")
MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="자연어 검색어")
    ap.add_argument("--category", default=None)
    ap.add_argument("--gender", default=None)
    ap.add_argument("--season", default=None)
    ap.add_argument("--price-max", type=int, default=None)
    ap.add_argument("-k", type=int, default=10)
    args = ap.parse_args()

    print(f"Loading {MODEL}...", file=sys.stderr)
    model = SentenceTransformer(MODEL)
    vec = model.encode([args.query], normalize_embeddings=True)[0]

    where = ["p.deleted_at IS NULL", "p.stock > 0"]
    params = []
    if args.category:
        where.append("p.category = %s"); params.append(args.category)
    if args.gender:
        where.append("p.gender IN (%s, '공용')"); params.append(args.gender)
    if args.season:
        where.append("p.season IN (%s, '사계절')"); params.append(args.season)
    if args.price_max:
        where.append("COALESCE(p.sale_price, p.price) <= %s"); params.append(args.price_max)

    sql = f"""
        SELECT p.id, p.name, p.category, p.subcategory, p.gender, p.season,
               p.style, p.color, COALESCE(p.sale_price, p.price) AS price,
               (e.embedding <=> %s) AS distance
        FROM products p
        JOIN product_embeddings e ON e.product_id = p.id
        WHERE {' AND '.join(where)}
        ORDER BY e.embedding <=> %s
        LIMIT %s
    """
    params = [vec, *params, vec, args.k]

    with psycopg.connect(DB_DSN) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    print(f"\n쿼리: {args.query}\n검색 결과 (가까울수록 유사도 ↑):\n")
    for i, r in enumerate(rows, 1):
        pid, name, cat, sub, gen, sea, sty, col, price, dist = r
        print(f"{i:>2}. [{1-dist:.3f}] {name}")
        print(f"     {cat}/{sub}, {gen}, {sea}, {sty}, {col}, {price:,}원")


if __name__ == "__main__":
    main()
