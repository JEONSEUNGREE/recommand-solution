# sparse-embedder (격리)

BGE-M3 **sparse(lexical_weights)** 전용 격리 컴포넌트. 기존 `embedder/`(dense, :8001)
와 **venv·포트·코드가 완전히 분리**되어 서로 영향이 없다.

## 왜 분리했나
- 기존 `embedder/.venv`: `transformers<4.50` + SentenceTransformer 로 dense 만 처리(검증됨).
- sparse 는 `FlagEmbedding==1.4.0` 이 필요한데, 이 버전은 `from_pretrained(dtype=)`
  를 쓰므로 **transformers>=4.56** 를 요구한다(4.56에서 도입). 기존 env 의 transformers
  를 올리면 잘 도는 dense 경로가 깨질 위험 → **별도 venv 로 격리**.
- 배경 전체: `../docs/hybrid-search-progress.md`

## 구성
| 파일 | 역할 |
|--|--|
| `sparse_model.py` | BGEM3FlagModel 싱글톤 + lexical_weights→pgvector sparsevec 변환 |
| `server.py` | FastAPI :8002 — `POST /embed-sparse` (검색 시 쿼리 인코딩용) |
| `backfill_sparse.py` | `product_descriptions.embedding_sparse` 백필(1회성) |
| `requirements.txt` | 격리 env 패키지 핀 |

## 데이터 안전
- 백필은 `product_descriptions.embedding_sparse` **새 컬럼만** UPDATE.
  `description` 을 읽어 인코딩하므로 dense 와 입력 텍스트 동일.
- `embedding_bge` / `embedding_openai` (dense) 는 **읽지도 쓰지도 않음**.
- 기본 대상은 `embedding_sparse IS NULL` 행만 → 재실행 멱등.

## 셋업
```powershell
# (저장소 루트의 .venv 와 무관한 별도 venv)
python -m venv sparse-embedder\.venv-sparse
sparse-embedder\.venv-sparse\Scripts\python -m pip install -U pip
sparse-embedder\.venv-sparse\Scripts\python -m pip install -r sparse-embedder\requirements.txt
```

## 사용
```powershell
cd sparse-embedder
# 1) 안전 점검 (DB 미기록, 인코딩만 + 속도 측정)
.venv-sparse\Scripts\python backfill_sparse.py --dry-run --limit 5
# 2) 백필 전체 (embedding_sparse IS NULL 인 행)
.venv-sparse\Scripts\python backfill_sparse.py
# 3) 검색 연동용 서비스 기동
.venv-sparse\Scripts\python -m uvicorn server:app --host 127.0.0.1 --port 8002
```

환경변수: `DB_DSN`(기본 `postgresql://app:app@localhost:5433/recommend`),
`EMBED_MODEL`(기본 `BAAI/bge-m3`).
