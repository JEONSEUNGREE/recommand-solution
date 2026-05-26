"""BGE-M3 공유 로더 — dense + sparse(+colbert)를 단일 모델 1회 로드로 제공.

BGE-M3는 forward pass 한 번에 세 표현을 모두 출력한다:
  - dense          : 1024d 정규화 벡터 (코사인 검색)
  - lexical_weights: {token_id: weight} 희소 가중치 (어휘 매칭)
  - colbert_vecs   : 토큰별 벡터 (late interaction, 현재 미사용)

모델은 프로세스당 1회 로드되어 메모리에 상주한다(~2.3GB). 검색 모드(dense/hybrid)는
같은 모델의 어떤 출력을 쓰느냐의 문제일 뿐, 모델을 다시 로드하지 않는다.
"""
from __future__ import annotations

import os
import threading

MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
DIM = 1024
# BGE-M3 토크나이저(XLM-RoBERTa) vocab 크기 = sparsevec 차원
SPARSE_DIM = 250002

_model = None        # FlagEmbedding (dense+sparse) — 하이브리드 보류분 전용
_st_model = None     # SentenceTransformer (dense) — 현재 운영 경로
_lock = threading.Lock()


def get_model():
    """BGEM3FlagModel 싱글톤. 최초 호출 시에만 로드(지연 로딩).

    ⚠️ 현재 설치된 transformers와 호환 안 됨
    (XLMRobertaModel.__init__() got an unexpected keyword argument 'dtype').
    하이브리드 sparse 경로가 이 모델을 쓰는데, 버전 정합 후에만 동작한다.
    dense 검색은 아래 get_st_model()(SentenceTransformer)을 쓴다.
    자세한 내용: docs/hybrid-search-progress.md
    """
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from FlagEmbedding import BGEM3FlagModel
                # use_fp16은 GPU 전용 — CPU 환경에선 False(fp32)
                _model = BGEM3FlagModel(MODEL_NAME, use_fp16=False)
    return _model


def get_st_model():
    """SentenceTransformer 싱글톤 — dense(1024d) 인코딩용. 검증된 운영 경로."""
    global _st_model
    if _st_model is None:
        with _lock:
            if _st_model is None:
                from sentence_transformers import SentenceTransformer
                _st_model = SentenceTransformer(MODEL_NAME)
    return _st_model


def encode(texts: list[str], *, dense: bool = True, sparse: bool = False,
           colbert: bool = False, batch_size: int = 16) -> dict:
    """원시 인코딩 결과 반환 (dense_vecs / lexical_weights / colbert_vecs).

    FlagEmbedding 경로 — sparse가 필요한 하이브리드 보류분에서만 사용.
    """
    m = get_model()
    return m.encode(
        texts, batch_size=batch_size,
        return_dense=dense, return_sparse=sparse, return_colbert_vecs=colbert,
    )


def encode_dense(texts: list[str]) -> list[list[float]]:
    """dense 1024d 정규화 벡터. SentenceTransformer 경로(현재 운영)."""
    vecs = get_st_model().encode(
        texts, normalize_embeddings=True, show_progress_bar=False
    )
    return [v.tolist() for v in vecs]


def lexical_to_sparsevec(lw: dict) -> str:
    """BGE-M3 lexical_weights({token_id(str): weight}) → pgvector sparsevec 리터럴.

    pgvector sparsevec 형식: '{idx:val,idx:val}/dim' (인덱스 1-based, 오름차순).
    BGE-M3 token_id는 0-based이므로 +1 변환한다. weight 0 이하는 제외.
    """
    items = []
    for tid, w in lw.items():
        w = float(w)
        if w <= 0:
            continue
        items.append((int(tid) + 1, w))
    items.sort(key=lambda x: x[0])
    if not items:
        # 전부 0이면 pgvector가 빈 sparsevec을 거부하므로 1번 인덱스에 0 하나 둔다
        return "{1:0}/%d" % SPARSE_DIM
    body = ",".join(f"{idx}:{w:.6f}" for idx, w in items)
    return "{%s}/%d" % (body, SPARSE_DIM)
