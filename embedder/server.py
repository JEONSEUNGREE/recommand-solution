"""임베딩 전용 사이드카.

Spring Boot가 자연어 검색어를 보내면 1024차원 벡터로 인코딩해서 반환.
모델은 프로세스 시작 시 한 번만 로드되어 메모리에 상주.
"""
import os

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-m3")

print(f"Loading {MODEL_NAME}...")
model = SentenceTransformer(MODEL_NAME)
DIM = model.get_sentence_embedding_dimension()
print(f"  → dim = {DIM}")

app = FastAPI(title="Embedder Sidecar")


class EmbedRequest(BaseModel):
    texts: list[str]


@app.post("/embed")
def embed(req: EmbedRequest):
    vecs = model.encode(req.texts, normalize_embeddings=True, show_progress_bar=False)
    return {
        "model": MODEL_NAME,
        "dim": DIM,
        "vectors": [v.tolist() for v in vecs],
    }


@app.get("/healthz")
def health():
    return {"ok": True, "model": MODEL_NAME, "dim": DIM}
