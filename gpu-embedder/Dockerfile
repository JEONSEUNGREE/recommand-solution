# BGE-M3 GPU 임베더 — RTX 2080 (Turing, compute capability 7.5) / CUDA 11.8
# 호스트에는 NVIDIA 드라이버만 있으면 된다(시스템 CUDA Toolkit 불필요).
# torch cu118 휠이 CUDA 런타임을 자체 포함하기 때문.
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    EMBED_MODEL=BAAI/bge-m3

# Ubuntu 22.04 기본 Python 3.10 + pip
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) GPU torch 먼저 설치 (cu118 휠).
RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install torch==2.2.2 --index-url https://download.pytorch.org/whl/cu118

# 2) 나머지 의존성
COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt

# 3) 앱
COPY model.py app.py ./

# (선택) 모델을 이미지에 미리 굽기 — 첫 기동 빨라지고 오프라인 가능, 대신 이미지 ~+2.5GB.
#        air-gapped 서버면 주석 해제 권장. 빌드머신에 인터넷 필요.
# RUN python3 -c "import pyarrow; from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3', use_fp16=False)"

EXPOSE 8002
# GPU 1장 기준 워커 1개(모델 상주 메모리 공유 안 되므로 워커↑ = VRAM 배수). 2080이면 1 권장.
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
