"""상품 텍스트 → 벡터 임베딩 → product_embeddings 테이블 upsert.

기본 모델: BAAI/bge-m3 (한국어 강력, 1024차원, 다국어)
허깅페이스에서 자동 다운로드됨 (~2.3GB).
"""
import os
import time
from typing import Iterable

import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

DB_DSN = os.getenv("DB_DSN", "postgresql://app:app@localhost:5433/recommend")
MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
BATCH = int(os.getenv("BATCH", "32"))
EXPECTED_DIM = int(os.getenv("EXPECTED_DIM", "1024"))


def build_text(row) -> str:
    """임베딩에 들어갈 텍스트 합성. 검색 의미에 영향 큰 필드 위주."""
    (name, brand, category, subcategory, gender, season,
     style, color, material, description, tags) = row
    return (
        f"{name}. 카테고리: {category} {subcategory}. "
        f"브랜드: {brand}. 성별: {gender}. 시즌: {season}. 스타일: {style}. "
        f"색상: {color}. 소재: {material}. "
        f"설명: {description} "
        f"태그: {', '.join(tags or [])}"
    )


def chunks(it: Iterable, n: int):
    buf = []
    for x in it:
        buf.append(x)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf


def main():
    print(f"Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    print(f"  → embedding dim = {dim}")
    assert dim == EXPECTED_DIM, f"schema expects vector({EXPECTED_DIM}), model dim={dim}"

    print(f"Connecting → {DB_DSN}")
    with psycopg.connect(DB_DSN, autocommit=False) as conn:
        register_vector(conn)

        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.name, p.brand, p.category, p.subcategory,
                       p.gender, p.season, p.style, p.color, p.material,
                       p.description, p.tags
                FROM products p
                LEFT JOIN product_embeddings e ON e.product_id = p.id
                WHERE p.deleted_at IS NULL AND e.product_id IS NULL
            """)
            rows = cur.fetchall()

        total = len(rows)
        print(f"To embed: {total}")
        if total == 0:
            return

        upsert_sql = """
            INSERT INTO product_embeddings (product_id, embedding, embedded_text, model)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (product_id) DO UPDATE
            SET embedding = EXCLUDED.embedding,
                embedded_text = EXCLUDED.embedded_text,
                model = EXCLUDED.model,
                updated_at = NOW()
        """

        done = 0
        t0 = time.time()
        for batch in chunks(rows, BATCH):
            ids = [r[0] for r in batch]
            texts = [build_text(r[1:]) for r in batch]
            vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

            with conn.cursor() as cur:
                cur.executemany(
                    upsert_sql,
                    [(pid, vec, txt, MODEL_NAME)
                     for pid, vec, txt in zip(ids, vectors, texts)],
                )
            conn.commit()
            done += len(batch)
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta = (total - done) / rate if rate else 0
            print(f"  embedded {done}/{total}  ({rate:.0f}/s, ETA {eta:.0f}s)")

    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
