"""
Step 4 · 임베딩 (embed_descriptions.py)

product_enriched.desc_situation/material/style/persona 4관점 텍스트를
임베딩 모델로 벡터화해 product_descriptions에 적재.

지원 모델 (둘 다 동시 적재 가능):
  - bge      → 로컬 embedder /embed (bge-m3, 1024d) → embedding_bge 컬럼
  - openai   → text-embedding-3-small (1536d) → embedding_openai 컬럼

사용:
  python -m embedder.embed_descriptions <rv_product_id> --backend bge
  python -m embedder.embed_descriptions <rv_product_id> --backend openai
  python -m embedder.embed_descriptions <rv_product_id> --backend both
  python -m embedder.embed_descriptions --batch --status extracted --limit 50 --backend both
"""

import argparse
import json
import os
import sys
from typing import Literal

import psycopg
import requests

from embedder.enrich_claude import DB_DSN  # noqa


PERSPECTIVES = ["situation", "material", "style", "persona"]
EMBEDDER_BASE = os.environ.get("EMBEDDER_BASE", "http://localhost:8001")
OPENAI_MODEL = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")


def embed_bge(texts: list[str]) -> list[list[float]]:
    """로컬 embedder의 /embed 호출 (bge-m3, 1024d)."""
    r = requests.post(f"{EMBEDDER_BASE}/embed", json={"texts": texts}, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["vectors"]


def embed_openai(texts: list[str]) -> list[list[float]]:
    """OpenAI text-embedding-3-small (1536d). OPENAI_API_KEY 필요."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY 환경변수 미설정")
    from openai import OpenAI
    client = OpenAI()
    r = client.embeddings.create(model=OPENAI_MODEL, input=texts)
    return [d.embedding for d in r.data]


def fetch_descriptions(conn, rv_product_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT rv_product_id, advertiser_id,
                      desc_situation, desc_material, desc_style, desc_persona
                 FROM product_enriched
                WHERE rv_product_id = %s""",
            (rv_product_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "rv_product_id": row[0],
            "advertiser_id": row[1],
            "situation": row[2],
            "material": row[3],
            "style": row[4],
            "persona": row[5],
        }


def upsert_description(conn, rv_id: int, adv_id: int, perspective: str, text: str,
                       bge_vec: list[float] | None, openai_vec: list[float] | None,
                       model: str):
    """둘 중 채워진 컬럼만 UPDATE. UPSERT (rv_product_id, perspective)."""
    with conn.cursor() as cur:
        # 먼저 INSERT 시도 (description은 항상 갱신, 벡터는 둘 다 NULL로 시작 가능)
        cur.execute(
            """
            INSERT INTO product_descriptions(rv_product_id, advertiser_id, perspective, description, model, embedding_bge, embedding_openai)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (rv_product_id, perspective) DO UPDATE SET
              description       = EXCLUDED.description,
              model             = COALESCE(EXCLUDED.model, product_descriptions.model),
              embedding_bge     = COALESCE(EXCLUDED.embedding_bge, product_descriptions.embedding_bge),
              embedding_openai  = COALESCE(EXCLUDED.embedding_openai, product_descriptions.embedding_openai),
              created_at        = NOW()
            """,
            (rv_id, adv_id, perspective, text, model, bge_vec, openai_vec),
        )
    conn.commit()


def embed_one(conn, rv_product_id: int, backend: Literal["bge", "openai", "both"]) -> dict:
    row = fetch_descriptions(conn, rv_product_id)
    if not row:
        raise RuntimeError(f"product_enriched not found: {rv_product_id}")

    persp_texts = [(p, (row.get(p) or "").strip()) for p in PERSPECTIVES]
    persp_texts = [(p, t) for p, t in persp_texts if t]
    if not persp_texts:
        return {"rv_product_id": rv_product_id, "embedded": 0, "backend": backend}

    texts = [t for _, t in persp_texts]
    bge_vectors: list[list[float]] | None = None
    openai_vectors: list[list[float]] | None = None

    if backend in ("bge", "both"):
        bge_vectors = embed_bge(texts)
    if backend in ("openai", "both"):
        openai_vectors = embed_openai(texts)

    model_label = backend if backend != "both" else "bge+openai"
    n = 0
    for i, (p, t) in enumerate(persp_texts):
        b = bge_vectors[i] if bge_vectors else None
        o = openai_vectors[i] if openai_vectors else None
        upsert_description(conn, row["rv_product_id"], row["advertiser_id"], p, t, b, o, model_label)
        n += 1

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE product_enriched SET enrich_status='embedded' WHERE rv_product_id=%s AND enrich_status IN ('extracted','validated','refined')",
            (rv_product_id,),
        )
    conn.commit()
    return {"rv_product_id": rv_product_id, "embedded": n, "backend": backend}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rv_product_id", type=int, nargs="?")
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--status", default="extracted")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--backend", choices=["bge", "openai", "both"], default="bge")
    args = ap.parse_args()

    conn = psycopg.connect(DB_DSN)

    if args.batch:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rv_product_id FROM product_enriched WHERE enrich_status=%s LIMIT %s",
                (args.status, args.limit),
            )
            ids = [r[0] for r in cur.fetchall()]
        print(f"[embed] batch: {len(ids)} rows, backend={args.backend}", file=sys.stderr)
        for rv_id in ids:
            try:
                r = embed_one(conn, rv_id, args.backend)
                print(f"  {rv_id} → embedded {r['embedded']} perspectives ({args.backend})", file=sys.stderr)
            except Exception as e:
                print(f"  {rv_id} ERR: {e}", file=sys.stderr)
    else:
        if not args.rv_product_id:
            ap.error("rv_product_id required (또는 --batch)")
        r = embed_one(conn, args.rv_product_id, args.backend)
        print(json.dumps(r, ensure_ascii=False))

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
