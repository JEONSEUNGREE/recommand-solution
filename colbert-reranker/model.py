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


def encode_colbert_with_tokens(text: str):
    """텍스트 → (토큰 문자열 리스트, ColBERT 벡터). 정렬뷰용.

    반환: (tokens: list[str], vecs: list[list[float]])  — 길이 동일.
    BGE-M3 토크나이저로 특수토큰([CLS]/[SEP] 등) 포함 토큰열을 디코딩한다.
    ColBERT 벡터 행 수와 토큰 수가 맞게 자른다.
    """
    m = get_model()
    out = m.encode([text], batch_size=1,
                   return_dense=False, return_sparse=False, return_colbert_vecs=True)
    vecs = np.array(out["colbert_vecs"][0], dtype=np.float32)[:, :COLBERT_DIM]
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = vecs / norms
    # 토큰 문자열 — FlagEmbedding 내부 tokenizer 로 id→토큰 복원
    tok = m.tokenizer
    ids = tok(text, add_special_tokens=True)["input_ids"]
    toks = tok.convert_ids_to_tokens(ids)
    n = min(len(toks), vecs.shape[0])
    return toks[:n], vecs[:n].tolist()


def align(q_text: str, d_text: str, top_k: int = 40) -> dict:
    """쿼리 각 토큰이 상품의 어떤 토큰과 가장 가까운지(MaxSim 정렬) 반환.

    반환: {alignments: [{q_token, best_d_token, sim}], score}
    """
    q_toks, q_vecs = encode_colbert_with_tokens(q_text)
    d_toks, d_vecs = encode_colbert_with_tokens(d_text)
    q = np.array(q_vecs, dtype=np.float32)
    d = np.array(d_vecs, dtype=np.float32)
    sims = q @ d.T                              # Q×K
    best_idx = sims.argmax(axis=1)
    best_sim = sims.max(axis=1)

    def _clean(t: str) -> str:
        # XLM-R 서브워드 prefix '▁' 제거, 특수토큰은 그대로 표기
        return t.replace("▁", "") or t

    aligns = []
    for i, qt in enumerate(q_toks):
        aligns.append({
            "q_token": _clean(qt),
            "best_d_token": _clean(d_toks[int(best_idx[i])]),
            "sim": round(float(best_sim[i]), 4),
        })
    # 특수토큰([CLS]/[SEP]/<s>/</s>) 행은 노이즈라 제외, 유사도 높은 순
    special = {"<s>", "</s>", "[CLS]", "[SEP]", "<pad>", "▁"}
    aligns = [a for a in aligns if a["q_token"] and a["q_token"] not in special]
    aligns.sort(key=lambda x: -x["sim"])
    return {"alignments": aligns[:top_k], "score": round(float(best_sim.sum()), 4)}
