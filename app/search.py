"""검색 코어: LLM으로 파싱한 필터 + 벡터 ANN을 SQL 한방에."""
from sentence_transformers import SentenceTransformer

from .db import get_conn

_model: SentenceTransformer | None = None
import os
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")


def embedder() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def search(filters: dict, semantic_query: str, k: int = 50) -> list[dict]:
    """필터 + 벡터 검색. 한 SQL로 처리."""
    vec = embedder().encode([semantic_query], normalize_embeddings=True)[0]

    where = ["p.deleted_at IS NULL"]
    params: list = []

    if filters.get("in_stock_only", True):
        where.append("p.stock > 0")
    if filters.get("category"):
        where.append("p.category = %s"); params.append(filters["category"])
    if filters.get("gender"):
        where.append("p.gender IN (%s, '공용')"); params.append(filters["gender"])
    if filters.get("season"):
        where.append("p.season IN (%s, '사계절')"); params.append(filters["season"])
    if filters.get("style"):
        where.append("p.style = %s"); params.append(filters["style"])
    if filters.get("color"):
        where.append("p.color = %s"); params.append(filters["color"])
    if filters.get("price_min") is not None:
        where.append("COALESCE(p.sale_price, p.price) >= %s"); params.append(filters["price_min"])
    if filters.get("price_max") is not None:
        where.append("COALESCE(p.sale_price, p.price) <= %s"); params.append(filters["price_max"])

    sql = f"""
        SELECT p.id, p.sku, p.name, p.brand, p.category, p.subcategory,
               p.gender, p.season, p.style, p.color, p.price, p.sale_price,
               p.stock, p.description,
               (e.embedding <=> %s) AS distance
        FROM products p
        JOIN product_embeddings e ON e.product_id = p.id
        WHERE {' AND '.join(where)}
        ORDER BY e.embedding <=> %s
        LIMIT %s
    """
    params = [vec, *params, vec, k]

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
