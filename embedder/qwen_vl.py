"""Qwen2-VL-2B-Instruct 로컬 CPU 캡셔너.

첫 호출 시 모델(~4GB)을 HF_HOME 경로로 다운로드.
- HF_HOME=D:/cache/huggingface 환경변수로 D 드라이브에 저장.
- CPU 추론: 이미지 1장당 10~30초 (RAM 4GB+ 사용).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import Image

_model = None
_processor = None
MODEL_ID = "Qwen/Qwen2-VL-2B-Instruct"


def _log(msg: str) -> None:
    # PowerShell cp949 콘솔에서 유니코드 특수문자(em-dash 등) 인코딩 실패 방지
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        print(f"[qwen_vl] {msg}", flush=True, file=sys.stdout)
    except UnicodeEncodeError:
        safe = msg.encode("ascii", "replace").decode("ascii")
        print(f"[qwen_vl] {safe}", flush=True, file=sys.stdout)


def get_model():
    global _model, _processor
    if _model is not None:
        return _model, _processor

    import torch
    from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

    t0 = time.perf_counter()
    _log(f"loading {MODEL_ID} (CPU, fp32) - first time downloads ~4GB to HF_HOME")
    _processor = AutoProcessor.from_pretrained(MODEL_ID)
    _model = Qwen2VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float32,
    )
    _model.to("cpu")
    _model.eval()
    _log(f"  loaded in {time.perf_counter() - t0:.1f}s")
    return _model, _processor


def caption(image_path: Path, prompt: str, max_new_tokens: int = 400) -> str:
    """이미지 1장에 대한 한국어 캡션 반환."""
    model, processor = get_model()
    import torch

    img = Image.open(image_path).convert("RGB")
    # 너무 큰 이미지는 미리 줄여서 시간 절약 (Qwen2-VL은 dynamic resolution 지원하지만 CPU에선 무거움)
    max_side = 768
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": img},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=[img], padding=True, return_tensors="pt")

    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )
    out = out[:, inputs.input_ids.shape[1]:]
    return processor.batch_decode(out, skip_special_tokens=True)[0]
