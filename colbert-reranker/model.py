"""BGE-M3 ColBERT 벡터 로더 — colbert-reranker 전용.

sparse-embedder(:8002)와 동일한 FlagEmbedding 환경(.venv-sparse)을 공유하거나
별도 venv를 만들어 사용한다. ColBERT 토큰 벡터를 COLBERT_DIM 차원으로 잘라
정규화하여 반환한다.

모델은 프로세스당 1회 지연 로딩(~2.3GB). CPU 환경.
"""
from __future__ import annotations

# ⚠️ 반드시 다른 무거운 네이티브 import 보다 먼저 — pyarrow load-order segfault 회피
import pyarrow  # noqa: F401

import os
import threading

import numpy as np

MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
# 저장할 ColBERT 차원 — 128d면 행당 ~30KB (1024d 원본의 1/8)
COLBERT_DIM = int(os.getenv("COLBERT_DIM", "128"))

_model = None
_lock = threading.Lock()


def get_model():
    """BGEM3FlagModel 싱글톤. 최초 호출 시에만 로드."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from FlagEmbedding import BGEM3FlagModel
                _model = BGEM3FlagModel(MODEL_NAME, use_fp16=False)
    return _model


def encode_colbert(texts: list[str], *, batch_size: int = 8) -> list[list[list[float]]]:
    """텍스트 리스트 → ColBERT 토큰 벡터 리스트.

    반환: [  [[tok1_d1, ...tok1_dD], [tok2_d1, ...]], ... ]
           문서별로 (num_tokens × COLBERT_DIM) float 배열.
    각 토큰 벡터는 COLBERT_DIM으로 잘라낸 뒤 L2 정규화.
    """
    m = get_model()
    out = m.encode(
        texts,
        batch_size=batch_size,
        return_dense=False,
        return_sparse=False,
        return_colbert_vecs=True,
    )
    result = []
    for vecs in out["colbert_vecs"]:
        arr = np.array(vecs, dtype=np.float32)          # (num_tokens, 1024)
        arr = arr[:, :COLBERT_DIM]                       # → (num_tokens, COLBERT_DIM)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms                                # L2 정규화
        result.append(arr.tolist())
    return result


def maxsim(q_vecs: list[list[float]], d_vecs: list[list[float]]) -> float:
    """ColBERT MaxSim 점수.

    각 쿼리 토큰에 대해 문서 토큰 중 최대 유사도를 구하고 합산.
    q_vecs: (Q, D), d_vecs: (K, D) — 이미 L2 정규화된 벡터.
    점수가 높을수록 관련성이 높다.
    """
    q = np.array(q_vecs, dtype=np.float32)   # Q×D
    d = np.array(d_vecs, dtype=np.float32)   # K×D
    sims = q @ d.T                            # Q×K
    return float(sims.max(axis=1).sum())
