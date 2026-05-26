"""ColBERT 리랭킹 동작 확인 — dense 순서 vs ColBERT MaxSim 순서 비교.

embedder(:8001) dense 검색으로 후보를 뽑고, colbert-reranker(:8003)로
쿼리 ColBERT 벡터를 받아 DB의 colbert_vecs와 MaxSim 점수를 계산해 재정렬한다.
실제 search_rv._colbert_rerank 와 같은 로직.
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import psycopg
import requests

DSN = "postgresql://app:app@localhost:5433/recommend"
EMBEDDER = "http://localhost:8001"
COLBERT = "http://localhost:8003"


def maxsim(q, d):
    q = np.array(q, dtype=np.float32)
    d = np.array(d, dtype=np.float32)
    return float((q @ d.T).max(axis=1).sum())


def run(query: str, k: int = 10):
    # 1) dense 검색 (baseline)
    r = requests.post(f"{EMBEDDER}/search-rv", json={"query": query, "k": k}, timeout=60)
    r.raise_for_status()
    items = r.json()["items"]
    print(f"\n쿼리: {query!r}  (dense top-{len(items)})")

    # 2) 쿼리 ColBERT 벡터
    qc = requests.post(f"{COLBERT}/embed-colbert", json={"text": query}, timeout=30).json()["colbert_vecs"]

    # 3) 후보 colbert_vecs 조회 + MaxSim
    pids = [it["rv_product_id"] for it in items]
    psps = [it["perspective"] for it in items]
    with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT rv_product_id, perspective, colbert_vecs
                 FROM product_descriptions
                WHERE (rv_product_id, perspective) IN (
                      SELECT unnest(%s::int[]), unnest(%s::text[]))
                  AND colbert_vecs IS NOT NULL""",
            (pids, psps),
        )
        vmap = {(row["rv_product_id"], row["perspective"]): row["colbert_vecs"] for row in cur.fetchall()}

    scored = []
    for it in items:
        d = vmap.get((it["rv_product_id"], it["perspective"]))
        score = maxsim(qc, d) if d else -float(it["distance"])
        scored.append((score, it))
    reranked = sorted(scored, key=lambda x: -x[0])

    # 4) 비교 출력
    print(f"{'#':>2} | {'dense 순서':<34} | dist   ||  {'ColBERT 재정렬':<34} | maxsim")
    print("-" * 110)
    for i in range(len(items)):
        d_it = items[i]
        c_score, c_it = reranked[i]
        dn = (d_it.get("product_name") or "")[:32]
        cn = (c_it.get("product_name") or "")[:32]
        moved = "" if d_it["rv_product_id"] == c_it["rv_product_id"] else "  <- 변동"
        print(f"{i+1:>2} | {dn:<34} | {d_it['distance']:.3f} ||  {cn:<34} | {c_score:.3f}{moved}")

    n_changed = sum(1 for i in range(len(items))
                    if items[i]["rv_product_id"] != reranked[i][1]["rv_product_id"])
    print(f"\n순위 변동: {n_changed}/{len(items)}건")


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "겨울에 따뜻하게 입을 니트"
    run(q)
