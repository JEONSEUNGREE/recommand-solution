"""BGE-M3 sparse(lexical_weights) 전용 로더 — 격리 환경.

기존 embedder/bge_model.py 와 같은 변환 규칙을 쓰되, 이쪽은 FlagEmbedding
(BGEM3FlagModel) 로 sparse 를 뽑는 것이 유일한 목적이다. dense 는 기존
embedder(SentenceTransformer, :8001)가 담당하므로 여기선 만들지 않는다.

모델은 프로세스당 1회 지연 로딩(~2.3GB). CPU 환경.
"""
from __future__ import annotations

# ⚠️ 반드시 다른 무거운 네이티브 import 보다 먼저 — pyarrow load-order segfault 회피
import pyarrow  # noqa: F401

import os
import threading

MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
# BGE-M3 토크나이저(XLM-RoBERTa) vocab 크기 = sparsevec 차원
SPARSE_DIM = 250002

_model = None
_lock = threading.Lock()


def get_model():
    """BGEM3FlagModel 싱글톤. 최초 호출 시에만 로드(지연 로딩)."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from FlagEmbedding import BGEM3FlagModel
                # use_fp16 은 GPU 전용 — CPU 환경에선 False(fp32)
                _model = BGEM3FlagModel(MODEL_NAME, use_fp16=False)
    return _model


def encode_sparse(texts: list[str], *, batch_size: int = 16) -> list[dict]:
    """텍스트 리스트 → lexical_weights 리스트 ({token_id: weight} 형태)."""
    m = get_model()
    out = m.encode(
        texts,
        batch_size=batch_size,
        return_dense=False,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    return out["lexical_weights"]


def lexical_to_sparsevec(lw: dict, *, min_token_chars: int = 1, tokenizer=None) -> str:
    """BGE-M3 lexical_weights({token_id: weight}) → pgvector sparsevec 리터럴.

    pgvector sparsevec 형식: '{idx:val,...}/dim' (인덱스 1-based, 오름차순).
    BGE-M3 token_id 는 0-based 이므로 +1 변환. weight 0 이하는 제외.
    전부 0이면 pgvector 가 빈 sparsevec 을 거부 → {1:0}/dim 폴백.

    min_token_chars > 1 이면 디코딩한 토큰 길이가 미만인 것은 제외.
    (예: BGE-M3 의 XLM-R 토크나이저가 한국어를 음절 단위로 쪼개므로 1자 단위
    의미없는 매칭을 끄고 싶을 때 2 로 설정. 양쪽 sparse 중 한쪽만 필터해도
    내적은 동일 — 곱셈에서 0 위치는 기여 0.)
    """
    items = []
    for tid, w in lw.items():
        w = float(w)
        if w <= 0:
            continue
        if min_token_chars > 1:
            if tokenizer is None:
                raise ValueError("min_token_chars>1 requires tokenizer")
            text = tokenizer.decode([int(tid)]).strip()
            if len(text) < min_token_chars:
                continue
        items.append((int(tid) + 1, w))
    items.sort(key=lambda x: x[0])
    if not items:
        return "{1:0}/%d" % SPARSE_DIM
    body = ",".join(f"{idx}:{w:.6f}" for idx, w in items)
    return "{%s}/%d" % (body, SPARSE_DIM)


def encode_sparsevec(texts: list[str], *, batch_size: int = 16,
                     min_token_chars: int = 1) -> list[str]:
    """텍스트 리스트 → pgvector sparsevec 리터럴 리스트.

    min_token_chars 가 1보다 크면 그 길이 미만 토큰은 제외(노이즈 컷).
    """
    m = get_model()
    out = m.encode(
        texts, batch_size=batch_size,
        return_dense=False, return_sparse=True, return_colbert_vecs=False,
    )
    tok = m.tokenizer if min_token_chars > 1 else None
    return [lexical_to_sparsevec(lw, min_token_chars=min_token_chars, tokenizer=tok)
            for lw in out["lexical_weights"]]
