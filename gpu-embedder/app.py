"""BGE-M3 GPU 임베딩 API (독립 실행 사이드카).

기존 embedder(:8001)와 무관하게 단독으로 뜬다. 기본 포트 8002.
dense / sparse / colbert 세 표현을 단일 엔드포인트에서 선택적으로 반환.
"""
# pyarrow를 가장 먼저 로드 — sentence_transformers/FlagEmbedding이 끌어오는
# pandas→pyarrow 네이티브 확장이 다른 무거운 import 뒤에 로드되면 access
# violation으로 죽는 케이스가 있다. 최우선 import로 회피. 없으면 무시.
try:
    import pyarrow  # noqa: F401
except Exception:
    pass

from fastapi import FastAPI
from pydantic import BaseModel, Field

import model as M

app = FastAPI(title="BGE-M3 GPU Embedder", version="1.0")


class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., description="인코딩할 문장들")
    dense: bool = True
    sparse: bool = False
    colbert: bool = False
    batch_size: int = 8
    max_length: int = 512


@app.get("/healthz")
def healthz():
    """모델/디바이스 상태. GPU에 제대로 올라갔는지 여기서 확인."""
    return {"ok": True, "model": M.MODEL_NAME, "dim": M.DIM, **M.device_info()}


@app.post("/warmup")
def warmup():
    """모델을 미리 로드(첫 /embed 지연 제거). 컨테이너 기동 후 1회 호출 권장."""
    M.get_model()
    return {"ok": True, **M.device_info()}


@app.post("/embed")
def embed(req: EmbedRequest):
    out = M.encode(
        req.texts,
        dense=req.dense,
        sparse=req.sparse,
        colbert=req.colbert,
        batch_size=req.batch_size,
        max_length=req.max_length,
    )
    return {"model": M.MODEL_NAME, "dim": M.DIM, "count": len(req.texts), **out}
