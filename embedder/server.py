"""임베딩 전용 사이드카.

Spring Boot가 자연어 검색어를 보내면 1024차원 벡터로 인코딩해서 반환.
모델은 프로세스 시작 시 한 번만 로드되어 메모리에 상주.
"""
# pyarrow를 가장 먼저 로드한다. sentence_transformers/FlagEmbedding은
# pandas→pyarrow 네이티브 확장을 끌어오는데, numpy 2.4 / pandas 3.0 / pyarrow 24
# 조합에서 sklearn·scipy가 먼저 로드된 뒤 pyarrow를 로드하면 access violation으로
# 죽는다. pyarrow를 최우선 import하면 충돌이 사라진다. (Windows, torch 2.11+cpu)
import pyarrow  # noqa: F401  # 반드시 다른 무거운 네이티브 import보다 먼저

import os

from dotenv import load_dotenv

# 프로젝트 루트의 .env 로드 — API 키 등. 라우터 import 전에 실행해야 함.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import bge_model
from .auth import cors_origins_with_dev_fallback, install_auth
from .scrape import router as scrape_router
from .enrich import router as enrich_router
from .enrich_api import router as enrich_llm_router
from .search_rv import router as search_rv_router
from .products_list import router as products_list_router, logs_router
from .rv_products_api import router as rv_products_router

MODEL_NAME = bge_model.MODEL_NAME
DIM = bge_model.DIM

# BGE-M3는 최초 /embed 호출 시 지연 로딩(서버 부팅 가속). 미리 로드하려면 아래 주석 해제.
# bge_model.get_model()

app = FastAPI(title="Embedder Sidecar")

# CORS: CORS_ORIGINS env CSV. 미설정 시 dev 폴백 (loopback/사설 대역).
# 프로덕션은 반드시 도메인 화이트리스트를 env로 명시.
_cors_origins = cors_origins_with_dev_fallback(
    default=(
        "http://localhost:3000",
        "http://localhost:5500",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5500",
        "http://192.168.101.27:3000",
        "http://192.168.101.27:5500",
    )
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bearer auth — CORS 뒤에 add 해서 request 처리 순서상 먼저 동작 (Starlette는 LIFO).
install_auth(app)

app.include_router(scrape_router)
app.include_router(enrich_router)
app.include_router(enrich_llm_router)
app.include_router(search_rv_router)
app.include_router(products_list_router)
app.include_router(logs_router)
app.include_router(rv_products_router)


class EmbedRequest(BaseModel):
    texts: list[str]
    sparse: bool = False  # True면 lexical_weights도 함께 반환


@app.post("/embed")
def embed(req: EmbedRequest):
    # dense는 검증된 SentenceTransformer 경로. sparse가 필요할 때만
    # FlagEmbedding 경로를 타는데, 현재 transformers 버전과 호환이 깨져 있어
    # 하이브리드(sparse)는 보류 상태다. (docs/hybrid-search-progress.md 참조)
    if not req.sparse:
        return {
            "model": MODEL_NAME,
            "dim": DIM,
            "vectors": bge_model.encode_dense(req.texts),
        }
    out = bge_model.encode(req.texts, dense=True, sparse=True)
    return {
        "model": MODEL_NAME,
        "dim": DIM,
        "vectors": [v.tolist() for v in out["dense_vecs"]],
        # token_id(int) → weight(float). JSON 직렬화 위해 키를 str로.
        "sparse": [
            {str(k): float(v) for k, v in lw.items()}
            for lw in out["lexical_weights"]
        ],
    }


@app.get("/healthz")
def health():
    return {"ok": True, "model": MODEL_NAME, "dim": DIM}
