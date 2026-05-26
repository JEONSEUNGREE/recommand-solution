"""BGE-M3 단일 모델 로더 — dense / sparse / colbert 세 표현을 한 번의 forward로.

BGE-M3는 forward pass 1회에 세 출력을 모두 낸다:
  - dense  : 1024d 정규화 벡터        (코사인 검색)
  - sparse : {token_id: weight}        (lexical_weights, 어휘 매칭)
  - colbert: 토큰별 1024d 벡터 행렬     (late interaction)

GPU가 있으면 fp16으로 로드(2080 8GB에 여유롭게 적재, 속도/메모리 이득).
모델은 프로세스당 1회 로드되어 상주한다(~2.3GB, fp16이면 ~1.2GB).
"""
from __future__ import annotations

import os
import threading

MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
DIM = 1024
# BGE-M3 토크나이저(XLM-RoBERTa) vocab 크기 = sparse 벡터의 전체 차원
SPARSE_DIM = 250002

_model = None
_lock = threading.Lock()


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def get_model():
    """BGEM3FlagModel 싱글톤. 최초 호출 시에만 로드(지연 로딩).

    use_fp16은 GPU 전용 — CUDA가 있으면 True(반정밀), CPU면 False(fp32).
    BGEM3FlagModel은 CUDA가 보이면 자동으로 GPU에 올린다.
    """
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from FlagEmbedding import BGEM3FlagModel
                _model = BGEM3FlagModel(MODEL_NAME, use_fp16=_cuda_available())
    return _model


def device_info() -> dict:
    """현재 실행 디바이스 정보 — /healthz 에서 GPU 적재 여부 확인용."""
    try:
        import torch
        if torch.cuda.is_available():
            return {
                "device": "cuda",
                "gpu": torch.cuda.get_device_name(0),
                "cuda": torch.version.cuda,
                "torch": torch.__version__,
                "fp16": True,
            }
        return {"device": "cpu", "torch": torch.__version__, "fp16": False}
    except Exception as e:  # torch 미설치 등
        return {"device": "unknown", "error": str(e)}


def encode(
    texts: list[str],
    *,
    dense: bool = True,
    sparse: bool = False,
    colbert: bool = False,
    batch_size: int = 8,
    max_length: int = 512,
) -> dict:
    """요청한 표현만 골라 JSON 직렬화 가능한 형태로 반환.

    반환 키(요청한 것만):
      dense  : list[list[float]]            — [N][1024]
      sparse : list[dict[str, float]]       — [N]{token_id(str): weight}
      colbert: list[list[list[float]]]      — [N][num_tokens][1024]

    max_length: colbert는 토큰 수에 비례해 메모리를 많이 쓴다. 긴 문서를
    colbert로 뽑을 땐 8192까지 올릴 수 있으나 2080 8GB에선 OOM 주의(batch_size↓).
    """
    m = get_model()
    out = m.encode(
        texts,
        batch_size=batch_size,
        max_length=max_length,
        return_dense=dense,
        return_sparse=sparse,
        return_colbert_vecs=colbert,
    )

    res: dict = {}
    if dense:
        res["dense"] = [v.tolist() for v in out["dense_vecs"]]
    if sparse:
        # token_id(int) → weight(float). JSON 키는 str 이어야 하므로 변환.
        res["sparse"] = [
            {str(k): float(v) for k, v in lw.items()}
            for lw in out["lexical_weights"]
        ]
    if colbert:
        res["colbert"] = [cv.tolist() for cv in out["colbert_vecs"]]
    return res
