# BGE-M3 GPU Embedder

`BAAI/bge-m3` 를 **GPU(RTX 2080, CUDA 11.8)** 에서 돌려 **dense / sparse / colbert** 세 표현을
한 엔드포인트에서 뽑는 독립 사이드카.

> **기존 서비스 영향 없음** — 별도 디렉터리, 별도 컨테이너, **포트 8002**(기존 embedder는 8001).
> 기존 코드/DB/설정 일절 건드리지 않는다. 이 폴더만 단독으로 빌드·실행된다.

---

## 0. 서버 사전 확인 (AI 팀에 요청)

```bash
nvidia-smi                 # 드라이버 + 우측 상단 "CUDA Version" = 지원 최대 CUDA
docker info | grep -i runtime   # 'nvidia' 보이면 컨테이너 GPU 사용 가능
# 안 보이면: nvidia-container-toolkit 설치 필요
```

- **RTX 2080 = Turing(cc 7.5)** → CUDA 11.x/12.x 전부 지원. cu118 기준 필요 드라이버: **Linux ≥ 450**.
- `nvidia-smi`의 CUDA Version이 **11.8 이상**이면 이 이미지 그대로 OK.

---

## 1. 빌드 & 실행

```bash
cd gpu-embedder

docker build -t bge-m3-gpu .

# --gpus all 로 GPU 노출. -v 로 모델 캐시 영속화(재기동 시 재다운로드 방지).
docker run -d --name bge-m3-gpu --gpus all \
  -p 8002:8002 \
  -v bge-models:/models \
  bge-m3-gpu

# 모델 미리 로드(첫 요청 지연 제거)
curl -X POST localhost:8002/warmup
```

> 첫 기동 시 HuggingFace에서 모델(~2.3GB)을 받는다. 인터넷이 막힌 서버면
> Dockerfile의 "모델 굽기" 줄을 주석 해제해 **빌드 머신에서** 받아 이미지에 포함시켜라.

---

## 2. GPU에 올라갔는지 확인

```bash
curl localhost:8002/healthz
```
```json
{ "ok": true, "model": "BAAI/bge-m3", "dim": 1024,
  "device": "cuda", "gpu": "NVIDIA GeForce RTX 2080", "cuda": "11.8", "fp16": true }
```
`"device": "cuda"` 면 성공. `"cpu"` 면 `--gpus all` 누락 or 드라이버 문제.

---

## 3. 세 형태 테스트

```bash
# dense 만
curl -X POST localhost:8002/embed -H 'content-type: application/json' \
  -d '{"texts":["겨울 패딩 점퍼"], "dense":true}'

# sparse(lexical) 만
curl -X POST localhost:8002/embed -H 'content-type: application/json' \
  -d '{"texts":["겨울 패딩 점퍼"], "dense":false, "sparse":true}'

# colbert(토큰별 벡터)
curl -X POST localhost:8002/embed -H 'content-type: application/json' \
  -d '{"texts":["겨울 패딩 점퍼"], "dense":false, "colbert":true}'

# 셋 다 한 번에
curl -X POST localhost:8002/embed -H 'content-type: application/json' \
  -d '{"texts":["겨울 패딩 점퍼","여름 린넨 셔츠"], "dense":true, "sparse":true, "colbert":true}'
```

### 응답 형태 (요청한 키만 포함)
| 키 | 타입 | 의미 |
|---|---|---|
| `dense`   | `[N][1024] float`             | 정규화 dense 벡터 |
| `sparse`  | `[N]{token_id(str): weight}`  | lexical_weights |
| `colbert` | `[N][num_tokens][1024] float` | 토큰별 late-interaction 벡터 |

---

## 4. API 파라미터

| 필드 | 기본 | 설명 |
|---|---|---|
| `texts` | — | 인코딩할 문장 배열 |
| `dense` / `sparse` / `colbert` | `true`/`false`/`false` | 뽑을 표현 선택 |
| `batch_size` | `8` | 배치 크기 |
| `max_length` | `512` | 토큰 상한. colbert로 긴 문서 뽑을 땐 ↑ 가능(최대 8192) |

> **VRAM 주의 (2080 8GB):** dense/sparse는 가볍다. **colbert + 긴 `max_length`** 조합은
> 토큰 수에 비례해 메모리를 많이 쓴다 → OOM 나면 `max_length`↓ 또는 `batch_size`↓.

---

## 5. 버전 핀 메모

`requirements.txt`의 `transformers==4.44.2` + `FlagEmbedding==1.2.11` 은 의도적 고정이다.
transformers ≥ 4.48 에서 `BGEM3FlagModel` 이
`XLMRobertaModel.__init__() got an unexpected keyword argument 'dtype'` 로 깨지기 때문.
업그레이드하려면 FlagEmbedding도 함께 1.3.x로 올려 호환 확인 후 진행할 것.
