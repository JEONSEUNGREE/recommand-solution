"""ColBERT Reranker FastAPI 서비스 (:8003).

기존 embedder(:8001, dense)·sparse-embedder(:8002, sparse)와 완전 분리된
ColBERT 전용 마이크로서비스.

역할:
  1. /embed-colbert  — 쿼리 텍스트 → ColBERT 토큰 벡터 반환 (search_rv.py가 호출)
  2. /rerank         — 쿼리 + 후보 colbert_vecs 목록을 받아 MaxSim 점수로 재정렬

실행:
  .venv-sparse/Scripts/python -m uvicorn server:app --host 0.0.0.0 --port 8003
  (colbert-reranker 디렉토리에서 실행, sparse-embedder와 동일한 .venv-sparse 공유 가능)
"""
from __future__ import annotations

import pyarrow  # noqa: F401 — load-order segfault 회피

from fastapi import FastAPI
from pydantic import BaseModel

import model as colbert_model

app = FastAPI(title="colbert-reranker", version="0.1.0")


# ──────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "service": "colbert-reranker",
        "model": colbert_model.MODEL_NAME,
        "colbert_dim": colbert_model.COLBERT_DIM,
    }


# ──────────────────────────────────────────────
# 쿼리 ColBERT 인코딩
# ──────────────────────────────────────────────

class EmbedColbertRequest(BaseModel):
    text: str
    batch_size: int = 8


@app.post("/embed-colbert")
def embed_colbert(req: EmbedColbertRequest):
    """텍스트 → ColBERT 토큰 벡터 리스트.

    응답: {"colbert_vecs": [[tok1_vec], [tok2_vec], ...], "num_tokens": N, "dim": D}
    """
    vecs = colbert_model.encode_colbert([req.text], batch_size=req.batch_size)[0]
    return {
        "colbert_vecs": vecs,
        "num_tokens": len(vecs),
        "dim": colbert_model.COLBERT_DIM,
    }


# ──────────────────────────────────────────────
# 배치 리랭킹
# ──────────────────────────────────────────────

class RerankItem(BaseModel):
    id: str | int
    colbert_vecs: list[list[float]]


class RerankRequest(BaseModel):
    query_colbert_vecs: list[list[float]]
    candidates: list[RerankItem]


@app.post("/rerank")
def rerank(req: RerankRequest):
    """쿼리 ColBERT 벡터 + 후보 목록 → MaxSim 점수로 재정렬.

    search_rv.py 가 dense top-K 결과를 여기 넘기면 ColBERT 점수로 순서를 바꿔서 반환.
    응답: {"results": [{"id": ..., "colbert_score": ...}, ...]}  (점수 내림차순)
    """
    scored = []
    for cand in req.candidates:
        score = colbert_model.maxsim(req.query_colbert_vecs, cand.colbert_vecs)
        scored.append({"id": cand.id, "colbert_score": round(score, 5)})
    scored.sort(key=lambda x: -x["colbert_score"])
    return {"results": scored}
