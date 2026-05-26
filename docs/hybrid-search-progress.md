# BGE-M3 하이브리드 검색 (dense + sparse) — 진행 현황

> 작성: 2026-05-26 · 갱신: 2026-05-26 · 상태: **sparse 백필 완료** —
> 하이브리드 검색 로직(α 가중합) 구현 단계. 아래 "해결: 격리 환경(sparse-embedder)" 참조.

기존 dense(1024d 코사인) 단일 검색에 sparse(어휘 가중치) 검색을 더해
**하이브리드 검색**으로 확장하려는 작업. 블로커(transformers 버전 충돌)를
**별도 격리 venv**로 우회 해결했고, `embedding_sparse` 4,224행 백필을 완료했다.

---

## 배경 / 동기

BGE-M3는 forward pass **한 번**에 세 가지 표현을 동시에 출력한다:

| 표현 | 형태 | 용도 |
|------|------|------|
| dense | 1024d 정규화 벡터 | 코사인 의미 검색 (현재 사용 중) |
| lexical_weights (sparse) | `{token_id: weight}` 희소 가중치 | 어휘 매칭 (추가하려는 것) |
| colbert_vecs | 토큰별 멀티벡터 | late interaction (미사용) |

dense만으로는 정확한 키워드/품번/고유명사 매칭이 약하므로, sparse 점수를
dense 코사인과 가중합하여 검색 품질을 올리는 것이 목표.

---

## 완료된 변경

### 1. 임베딩 엔진 교체: `sentence-transformers` → `FlagEmbedding`
- 이유: `SentenceTransformer`로는 sparse(lexical_weights)를 뽑을 수 없음.
  BGE-M3의 세 표현을 모두 얻으려면 `FlagEmbedding.BGEM3FlagModel`이 필요.

### 2. `embedder/bge_model.py` (신규)
- BGE-M3 **싱글톤 로더** (~2.3GB, 프로세스당 1회 지연 로딩).
- `encode(texts, dense=, sparse=, colbert=)` — 원시 출력 반환.
- `encode_dense()` — dense 벡터 리스트.
- `lexical_to_sparsevec(lw)` — BGE lexical_weights → pgvector `sparsevec` 리터럴 변환.
  - 형식: `{idx:val,...}/dim`, 인덱스 **1-based 오름차순**.
  - BGE token_id는 0-based → **+1** 변환. weight ≤ 0 제외.
  - 전부 0이면 pgvector가 빈 sparsevec을 거부 → `{1:0}/250002` 폴백.
- `SPARSE_DIM = 250002` (XLM-RoBERTa vocab 크기 = sparse 차원).

### 3. `embedder/server.py` (수정)
- 상단 `sentence_transformers` import 제거 → `bge_model` 사용.
- `bge_model.get_model()`은 **지연 로딩**이라 서버 부팅이 빨라짐
  (최초 `/embed` 호출 시에만 모델 로드).
- `/embed`에 `sparse: bool = False` 옵션 추가:
  - `sparse=True`면 응답에 `sparse: [{token_id(str): weight}]` 포함.
- 부수 작업으로 CORS · Bearer auth · 다수 라우터
  (scrape / enrich / search_rv / products_list / rv_products) 통합.

### 4. `migrations/018_product_descriptions_sparse.sql` (신규)
```sql
ALTER TABLE product_descriptions
    ADD COLUMN IF NOT EXISTS embedding_sparse sparsevec(250002);
```
- 인덱스 없음 — 수천 행 규모라 brute-force 스캔으로 충분.
- (pgvector 0.8의 sparsevec HNSW는 이 규모에서 불필요.)

---

## 블로커: pyarrow 로드 순서 충돌 — **해결됨 (2026-05-26)**

`sentence_transformers`·`FlagEmbedding` import 시 Segmentation fault
(access violation, `0xC0000005` / exit 139)로 죽어 임베더 서버가 부팅조차
못 했다. `bench.txt`가 `step: importing FlagEmbedding`에서 멈춘 것도 이 때문.

### 근본 원인
faulthandler 트레이스로 크래시 지점을 특정:

```
sentence_transformers → sklearn → pandas → pandas.compat.pyarrow
  → pyarrow/__init__.py:71  (네이티브 확장 create_module 중 access violation)
```

`import pyarrow` **단독은 정상**이지만, sklearn·scipy·numpy가 먼저 로드된 뒤
pyarrow 네이티브 확장을 로드하면 죽는다. **로드 순서 의존 바이너리 충돌**.
환경: numpy 2.4.4 / pandas 3.0.3 / pyarrow 24.0.0 / torch 2.11.0+cpu (전부 최신).
FlagEmbedding 설치가 torch/numpy/pandas를 끌어올리며 깨진 것으로 추정
(메모리상 5/13엔 sentence-transformers만으로 정상 동작).

### 적용한 해결책
`embedder/server.py` **최상단에 `import pyarrow` 한 줄** 추가 → 다른 무거운
네이티브 import보다 먼저 pyarrow를 로드시켜 충돌 회피. 검증:
`import pyarrow; import sentence_transformers` / `... import FlagEmbedding` 모두 OK.
서버 정상 기동, `/healthz` → `{"ok":true,"model":"BAAI/bge-m3","dim":1024}`.

> `KMP_DUPLICATE_LIB_OK=TRUE`는 효과 없었음(OpenMP 중복 문제가 아님).

## 블로커 2: FlagEmbedding ↔ transformers 버전 충돌 — sparse 보류, dense는 ST로 우회

pyarrow 문제를 해결하고 서버가 뜬 뒤, `/search-rv`·`/embed`가 500을 뱉었다.
두 가지 원인이 있었다:

1. **리팩토링 누락** — `search_rv.py._bge_model()`이 제거된 전역
   `embedder.server.model`(구 SentenceTransformer)을 참조 →
   `AttributeError: module 'embedder.server' has no attribute 'model'`.
2. **FlagEmbedding 비호환** — `bge_model.encode()`(FlagEmbedding 경로) 호출 시
   `TypeError: XLMRobertaModel.__init__() got an unexpected keyword argument 'dtype'`.
   FlagEmbedding이 모델 생성자에 `dtype=`를 넘기는데 현재 transformers가 안 받음.
   (또 버전 드리프트.)

### 적용한 결정 — dense는 SentenceTransformer, sparse(FlagEmbedding)는 보류
- `bge_model.encode_dense()`를 **SentenceTransformer 싱글톤**(`get_st_model()`)으로
  변경. ST는 이 환경에서 BGE-M3 dense를 정상 로드/인코딩함(검증: `(1, 1024)`).
- `bge_model.encode()`(FlagEmbedding, sparse 포함)는 함수로 남겨두되 **현재 깨짐**.
  하이브리드 sparse 경로가 이걸 쓰므로, transformers 버전 정합 후에만 동작.
- `server.py /embed`: `sparse=false`면 ST(`encode_dense`), `sparse=true`면
  FlagEmbedding 경로(보류). `search_rv` dense 검색은 ST로 정상 동작.
- 검증: `/search-rv {"query":"...","k":3}` → 관련 14K 귀걸이 3건 정상 반환.

### 아직 남은 정리 사항
1. **`requirements.txt`에 `FlagEmbedding` 미등재** + numpy/pandas/pyarrow/torch/
   **transformers** 버전 미핀. 하이브리드 sparse를 살리려면 FlagEmbedding이
   요구하는 transformers 버전으로 정합 + 핀 필요. (현재 dense만 ST로 우회 동작.)
2. 첫 검색/`/embed` 호출 시 **BGE-M3(~2.3GB) 지연 로딩**으로 수십 초 — CPU라 느림.
3. sparse 백필·하이브리드 가중합은 블로커 2 해소(transformers 정합) 후 진행.

---

## 해결: 격리 환경(sparse-embedder) — 2026-05-26 이어서

블로커 2(FlagEmbedding ↔ transformers `dtype` 충돌)를 기존 환경에서 고치면
잘 도는 dense(SentenceTransformer) 경로가 깨질 위험이 있어, **완전히 분리된
별도 컴포넌트**로 우회했다.

### 근본 원인 확정
- FlagEmbedding 1.4.0 의 `runner.py`가 `AutoModel.from_pretrained(dtype=...)` 호출.
- `dtype=` 인자는 **transformers 4.56.0**부터 도입(이전엔 `torch_dtype`).
- 기존 `.venv`는 transformers **4.49.0**(requirements.txt가 `<4.50`으로 고정) →
  `dtype`가 모델 `__init__`으로 흘러가 `XLMRobertaModel.__init__() got an
  unexpected keyword 'dtype'` 크래시. **즉 transformers가 너무 낮아서** 난 문제.
- 추가 발견: 기존 `.venv`는 이미 requirements 핀에서 드리프트
  (numpy 2.4.4 / torch 2.11.0 설치됨 — 핀은 numpy<2 / torch 2.2.2). 더 건드릴수록 위험.

### 신규 컴포넌트 `sparse-embedder/` (격리)
| 파일 | 역할 |
|--|--|
| `sparse_model.py` | BGEM3FlagModel 싱글톤 + lexical_weights→sparsevec 변환(기존 규칙 동일) |
| `server.py` | FastAPI **:8002** — `POST /embed-sparse` (검색 시 쿼리 인코딩용) |
| `backfill_sparse.py` | `product_descriptions.embedding_sparse` 만 UPDATE(멱등, dense 무손상) |
| `.venv-sparse` | 별도 venv: transformers **5.9.0** + FlagEmbedding 1.4.0 + torch 2.12.0 (CPU) |

- 기존 `embedder/`(:8001, dense)·`.venv`·dense 컬럼 일절 안 건드림.
- dense 와 sparse 는 **같은 BGE-M3 모델**(HF 캐시 공유). 로더(ST vs FlagEmbedding)만 다름.
- pyarrow load-order segfault 회피: 두 스크립트 최상단 `import pyarrow`.

### 백필 결과 (검증됨)
- 입력: `product_descriptions.description`(dense 와 동일한 4관점 텍스트) 그대로 사용.
- 속도(CPU): **~2.8행/s**, 4,224행 **약 25.6분** 소요.
- 검증: `total=4224, embedding_bge=4224, embedding_sparse=4224, 미백필=0`.
  sparsevec 형식 정상(1-based, 상품당 비0 토큰 50~80개).

---

## 남은 작업 (백필 완료 후)

1. ✅ **백필** — `product_descriptions` 4,224행 `embedding_sparse` 채움 (위 참조).
2. **`search_rv` 하이브리드 검색 로직** — dense 코사인(`<=>`) + sparse 내적(`<#>`)
   결합. 검색 시 쿼리를 :8002 `/embed-sparse`로 인코딩(기존 :8001은 FlagEmbedding
   불가). 점수 결합은 RRF 또는 정규화 α-가중합. `chat.html`에 dense/hybrid 토글.
3. **검색 품질 평가** — `docs/search_quality_test_queries.md` 쿼리셋으로
   dense-only vs hybrid 비교 (`search_logs`/`search_log_results` 활용).
