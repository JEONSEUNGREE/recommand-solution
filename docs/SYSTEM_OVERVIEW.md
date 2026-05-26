# `recommand-solution` 시스템 개요

> 작성: 2026-05-20 · Fullstack Engineer (VIT-2 인수 분석)
> 목적: 신규 인수받은 AI 상품추천 솔루션의 전체 구조·데이터 흐름·운영 상태를 다음 개발자가 즉시 작업할 수 있도록 정리.
> 본 문서는 코드/마이그레이션/실제 기동 결과를 직접 확인한 결과만 담음. 추측은 별도 표기.

---

## 0. TL;DR (CEO 30초 요약)

- **무엇**: 광고주(쇼핑몰) 상품을 멀티모달 LLM으로 분석해서 정형 속성+다관점 임베딩으로 적재해두고, 사용자 자연어 검색을 LLM이 정제·필터화한 뒤 pgvector로 검색하는 시스템.
- **주요 컴포넌트 4개**: Spring Boot backend(:8090, 광고주 동기화·이미지 서빙·검색 API), Python embedder/FastAPI(:8001, 임베딩+LLM 파싱+검색+enrich 워크플로), Next.js frontend(:3000, 사용자/운영자 UI), 정적 admin HTML(localhost 직접 호출). DB는 Postgres + pgvector(:5433).
- **핵심 인사이트**: 본 시스템의 "두뇌"는 backend가 아니라 **embedder (Python)**. 검색·LLM 파싱·이미지/HTML 스크랩·enrich가 모두 여기 있음. Spring backend는 광고주 CRUD + 메이크샵 OpenAPI 동기화 + enrich 워커 컨트롤러 + 이미지 정적 서빙 담당.
- **상태**: 4개 서비스 모두 로컬에서 정상 기동 중 (5433/8001/8090/3000 LISTEN 확인). luvreparis 1개 광고주 기준 enriched 1,056개 상품 적재. `/healthz`, `/search-rv` 라이브 응답 확인.
- **가장 큰 리스크**: (인증 — VIT-3 에서 해소) · (search_logs migrations — VIT-4 에서 해소) · 코드베이스 곳곳에 dev 하드코딩(localhost, 192.168.101.27) · 정리 안된 tmp_*.py / check_gender.py / embedder_err.log 잔존.

---

## 1. 시스템 개요 — 컴포넌트 다이어그램

```
                               ┌─────────────────────────────────────┐
                               │  사용자 (브라우저)                  │
                               │  - 검색 화면 (Next /)               │
                               │  - 운영자 화면 (Next /sync /products)│
                               │  - 정적 admin (admin/*.html)        │
                               └────┬────────────────────────┬───────┘
                                    │                        │
              ┌─────────────────────┘                        │ (admin은 :8001/:8090 직접 호출)
              ▼                                              │
      ┌──────────────────┐         ┌──────────────────────────────┐
      │ Next.js frontend │         │  HTTP                          │
      │ :3000 (16.2.4)   │         │                                │
      │ - app/page.tsx   │  rewrite /api/embedder/* → :8001         │
      │   (검색)         │  직호출  http://localhost:8090            │
      │ - app/sync, ...  │─────┬──────────────────┬─────────────────┘
      └──────────────────┘     │                  │
                               ▼                  ▼
                ┌────────────────────────┐  ┌──────────────────────────────┐
                │ Python embedder        │  │ Spring Boot backend          │
                │ FastAPI :8001          │  │ :8090 (Java 21, gradle)      │
                │                        │  │                              │
                │ /embed      (bge-m3)   │  │ /recommend  (LLM+search)     │
                │ /search-rv  (bge/openai)  │ /search     (벡터만)         │
                │ /search-rv-llm (LLM정제) │ │ /enrich (사이드카 프록시)    │
                │ /products-rv/*            │ /advertisers (CRUD+동기화)   │
                │ /advertisers-rv (목록)    │ /advertisers/{id}/products/* │
                │ /scrape (광고주 HTML 파싱)│   ├─ batch-fetch  (스크랩)   │
                │ /enrich (OCR/VLM)         │   ├─ batch-enrich (LLM)     │
                │ /products-enriched/*      │   ├─ images/{sha1}.jpg (정적)│
                │ /search-logs/*            │   └─ image-blocks            │
                │                           │ /advertisers/{id}/selectors  │
                │ + 배치 스크립트:          │                              │
                │  enrich_claude.py (Sonnet)│                              │
                │  validate_claude.py        │                              │
                │  embed_descriptions.py     │                              │
                └────────────┬───────────────┘  └────────────┬─────────────┘
                             │                               │
                             │           JDBC/psycopg        │
                             └──────────────┬────────────────┘
                                            ▼
                       ┌────────────────────────────────────────────────┐
                       │  Postgres 16 + pgvector (recommend-pg :5433)   │
                       │                                                │
                       │  코어 테이블:                                  │
                       │  - advertisers / advertiser_selectors          │
                       │    / advertiser_image_blocks                   │
                       │  - rv_products (광고주별 원본 상품)            │
                       │  - rv_product_images / rv_product_excluded_images │
                       │  - product_enriched (LLM 추출 결과)            │
                       │  - product_descriptions (4관점, vec 1024/1536) │
                       │  - product_sync_status                         │
                       │  - search_logs / search_log_results            │
                       │  (legacy/v0) products / product_embeddings(1024) │
                       └────────────────────────────────────────────────┘
                                            ▲
                                            │  파일/외부 호출
       ┌──────────────────┐  ┌──────────────┴──────────────┐  ┌─────────────────────┐
       │ 외부 LLM API     │  │  $RECOMMAND_DATA_DIR        │  │ 외부 쇼핑몰         │
       │ - Groq Llama-3.3 │  │  (default D:/recommand-data)│  │ - makeshop OpenAPI  │
       │ - OpenAI         │  │  advertisers/{id}/products/ │  │ - (cafe24/godo TBD) │
       │ - Anthropic      │  │   {code}/source.html        │  │                     │
       │   (Claude CLI)   │  │   /extracted.json           │  │                     │
       └──────────────────┘  │   /images/{sha1}.jpg        │  └─────────────────────┘
                             └─────────────────────────────┘
```

### 1.1 컴포넌트 역할 (한 단락씩)

- **frontend/ (Next.js 16.2.4, React 19, TS, Tailwind v4)**: 사용자/운영자 SPA. `app/page.tsx`는 일반 검색 UI(LLM on/off, bge/openai 토글, 광고주 선택, 결과 카드 + 상세 모달). `app/sync` / `app/sync/[advertiserId]/products`는 광고주 등록·셀렉터 관리·동기화 트리거·스크랩/Enrich 배치 진행률 모니터링. `app/products`는 enrich된 상품 검색·endorser 태깅·search_logs 통계. **주의: 커스텀 포크인 Next.js이므로 `frontend/AGENTS.md` 가 명시한 대로 `node_modules/next/dist/docs/`를 먼저 읽고 코드를 짜야 함.** `next.config.ts`는 `/api/embedder/*` → `http://localhost:8001/*` rewrite + `allowedDevOrigins: ["192.168.101.27"]` 하드코딩.

- **backend/ (Spring Boot 3.5, Java 21, Gradle)**: 두 가지 책임. (a) 데모용 v0 추천 파이프라인 — `/recommend`, `/search` (legacy `products`/`product_embeddings` 테이블 기반, OpenAI-호환 LLM(Groq 기본)로 자연어 파싱→bge 임베딩→pgvector cosine 정렬→LLM rerank). (b) 운영 도메인 — `advertisers` CRUD, 메이크샵 OpenAPI 동기화(`ProductSyncService`, 500개/페이지, 최대 200페이지 안전핀), enrich 배치 컨트롤러(스크랩 fetch + Claude/Haiku 멀티모달 enrich를 백그라운드 데몬 스레드로 실행 + 상태/취소/진행률 endpoint), 광고주 셀렉터/이미지 블록 관리, 광고주별 이미지 정적 서빙(`/advertisers/{id}/products/{code}/images/{sha1}.jpg`). (b) 쪽의 실제 추출 로직은 embedder를 사이드카로 호출.

- **embedder/ (Python 3.11, FastAPI, sentence-transformers, psycopg)**: 시스템의 진짜 두뇌. 프로세스 시작 시 `BAAI/bge-m3` 모델(1024d) 메모리 상주. 라우터: `scrape`(광고주 페이지 HTML 가져와서 셀렉터/앵커로 본문 + 이미지 다운로드), `enrich`(OCR/VLM/Claude 통합 호출), `enrich_api`/`enrich_llm_router`(LLM 단일 상품 enrich + 이미지 블록 관리), `search_rv`(자연어→LLM 파싱→임베딩→pgvector 검색, 키워드/PE 정형 필터 2단계 완화 로직), `products_list`/`logs_router`(enrich된 상품 검색 + search_logs 조회), `rv_products_api`(rv_products 페이지네이션 + endorser 태깅). 배치 CLI는 `enrich_claude.py`(통합 추출 — Sonnet/Haiku), `validate_claude.py`(검증), `embed_descriptions.py`(4관점 OpenAI 임베딩, 1536d 적재), `batch_enrich_resilient.py` 등.

- **admin/ (static HTML/JS)**: Pretendard 폰트 + vanilla JS. 광고주 선택을 localStorage `aira_adv`에 저장하는 가벼운 관리자 패널. 실질적 인증 없음. `dashboard.html`, `products.html`, `chat.html`, `cost.html`, `extractions.html`, `upload.html` 등. **모든 API URL이 `http://localhost:8001` / `http://localhost:8090` 하드코딩**. `dashboard.html`에서 `BACKEND = SEARCH = 'http://localhost:8001'`로 둘 다 embedder를 가리키는 점도 운영자 패널 의도일 수 있으나 backend(:8090) 통계 endpoint도 따로 있는 만큼 일관성 확인 필요.

- **db/ (Postgres 16 + pgvector)**: `docker-compose.yml`의 `pgvector/pgvector:pg16` 이미지가 `:5433`(host)→`:5432`(container) 매핑. `migrations/*.sql`이 컨테이너 entrypoint(`/docker-entrypoint-initdb.d`)로 자동 실행. `db/recommend.dump`는 pg_dump custom format(10k 의류 상품 + bge-m3 임베딩 포함) — `pg_restore --clean --if-exists`로 복원.

- **scripts/, embedder/ root**: 데이터 시드/임베딩/스모크 테스트(`seed_products.py`, `embed_products.py`, `smoke_search.py`, `setup_adv3.py`, `verify_adv3.py`). `embedder/scrape/`도 있음.

- **루트의 tmp_*.py / check_gender.py / migrate_search_logs_v2.py / setup_search_logs.py / embedder_err.log / embedder.log**: 정리되지 않은 일회성 스크립트와 로그(§6 리스크 참조).

---

## 2. 데이터 흐름 — 사용자 검색 → 추천 결과

### 2.1 두 가지 검색 파이프라인이 공존함

| 파이프라인 | 진입점 | 호출 흐름 | 임베딩 테이블 | 상태 |
|--|--|--|--|--|
| **v0 데모** | `POST :8090/recommend` (`RecommendController.recommend`) | Spring → OpenAI-호환 LLM(QueryParser) → :8001/embed → pgvector `product_embeddings` → LLM rerank | `products` + `product_embeddings(1024d)` | legacy, 데모/스모크용 |
| **v1 운영** | `POST :8001/search-rv-llm` (FastAPI) — frontend가 직접 호출 | embedder 내부에서 모두 처리: LLM 파싱(Groq/OpenAI) → bge 또는 OpenAI 임베딩 → pgvector `product_descriptions` 4관점 평균거리 → 2단계 키워드/PE 필터 완화 | `product_descriptions(embedding_bge 1024d / embedding_openai 1536d)` | **실사용 경로** |

> 결정: 프론트(`app/page.tsx`, `app/search-rv/page.tsx`)는 모두 embedder의 `/search-rv-llm`을 직접 호출. Spring 의 `/recommend`는 현재 UI에서 호출되지 않음 (smoke/디버깅용으로 남아 있음). 신규 기능은 v1 라인에 붙이는 것이 안전.

### 2.2 v1 운영 파이프라인 상세 (실제 사용 경로)

```
[Next.js app/page.tsx]
  query="봄에 데이트하기 좋은 여성 14k 귀걸이 5만원대"
  advertiser_id=1, backend="bge", llm_provider="groq", k=10
       │
       │  POST /api/embedder/search-rv-llm
       │  (next.config.ts rewrite → http://localhost:8001/search-rv-llm)
       ▼
[embedder/search_rv.py · search_rv_llm]
  ① _llm_parse_query(query, "groq")
       Groq Llama-3.3-70b · response_format=json_object
       → {
           "semantic_query": "봄 데이트 14k 귀걸이",
           "filters": {
             "target_gender": "여성",
             "category_contains": "귀걸이",
             "perspective_preference": "situation",
             "price_min": 45000, "price_max": 55000,
             "keyword_filters": []
           },
           "notes": "...",
           "_usage": {"input_tokens": ..., "output_tokens": ..., "cost_usd": ...}
         }
  ② _embed_bge("봄 데이트 14k 귀걸이")
       sentence-transformers SentenceTransformer.encode([text], normalize=True)
       → list[float] (1024d)
  ③ SQL:
       WITH all_dist AS (
         SELECT pd.rv_product_id, pd.advertiser_id, pd.perspective, pd.description,
                pd.embedding_bge <=> $1::vector AS distance
           FROM product_descriptions pd
           JOIN rv_products rv_p ON rv_p.id = pd.rv_product_id    -- 가격 필터 있을 때만
           LEFT JOIN product_enriched pe ON pe.rv_product_id = pd.rv_product_id
          WHERE pd.embedding_bge IS NOT NULL
            AND pd.advertiser_id = $2
            AND COALESCE(rv_p.sale_price, rv_p.price) BETWEEN 45000 AND 55000
            AND (pe.target_gender = '여성'
                 AND pe.category ILIKE '%귀걸이%')
       ), ranked AS (
         SELECT rv_product_id, advertiser_id,
                AVG(distance) AS distance,
                (array_agg(perspective ORDER BY distance))[1] AS perspective,
                (array_agg(description ORDER BY distance))[1] AS description
           FROM all_dist
          GROUP BY rv_product_id, advertiser_id
       )
       SELECT r.*, rv.product_code, rv.product_name, rv.image_url, rv.price, rv.sale_price,
              pe.category, pe.brand, pe.brand_tier, pe.target_gender,
              pe.target_age_min, pe.target_age_max, pe.tags, pe.desc_persona
         FROM ranked r
         JOIN rv_products rv ON rv.id = r.rv_product_id
         LEFT JOIN product_enriched pe ON pe.rv_product_id = r.rv_product_id
        ORDER BY r.distance ASC
        LIMIT $K;
  ④ 결과 0건이면 2단계 완화:
       - 1단계: keyword_filters 제거 후 재실행
       - 2단계: PE 정형 필터 전부 제거(brand_tier/gender/age/category) — keyword_filter_relaxed/pe_filter_relaxed 플래그로 응답에 표시
  ⑤ search_logs + search_log_results 적재 (랭킹·distance·tags 스냅샷 + LLM 비용·토큰·duration)
  ⑥ 응답: items[] + parsed{} + debug_sql + duration_ms
       │
       ▼
[Next.js] LLM 해석 바 + 결과 카드 + 클릭 시 상세 모달
         (상세 모달은 GET /api/embedder/products-rv/{id}/detail 호출)
```

### 2.3 데이터 적재 흐름 (검색 가능 상태가 되기까지)

```
[광고주 등록]    POST :8090/advertisers (admin 또는 frontend /sync)
                 (advertisers + advertiser_selectors + image_blocks 세팅)
        │
[셀렉터 동기화]  POST :8090/advertisers/{id}/sync
                 → ProductSyncService.syncMakeshop (메이크샵 OpenAPI 페이지네이션, 500개씩)
                 → rv_products UPSERT (uid 기준), product_sync_status 진행률
        │
[스크랩]         POST :8090/advertisers/{id}/products/batch-fetch?mode=page|api&...
                 → ProductEnrichService.batchFetch* (백그라운드 스레드)
                 → embedder /scrape 호출 → source.html + extracted.json + images/{sha1}.jpg
                 → rv_products.scrape_status='scraped', html_path/image_dir 갱신
                 → rv_product_images 인덱스 + advertiser_image_blocks로 광고배너 차단
        │
[LLM enrich]     POST :8090/advertisers/{id}/products/batch-enrich?model=haiku|sonnet&...
                 → embedder enrich_claude.py 동등 로직(통합 호출): Pass1 이미지 필터(haiku)
                   + Pass2 본 분석(haiku/sonnet) — JSON schema 8920 토큰 평균
                 → product_enriched (category, brand_tier, target_gender, tags,
                   desc_situation/material/style/persona, image_types, cost_usd, duration_ms 등)
                 → enrich_status='extracted'
        │
[검증(선택)]     validate_claude.py — Haiku로 trust_score + trap_detected
                 → enrich_status='validated', validation_*
        │
[임베딩]         embed_descriptions.py — 4관점 텍스트만 OpenAI text-embedding-3-small (1536d)
                 또는 bge-m3 (1024d, 로컬)로 인코딩
                 → product_descriptions(perspective, embedding_openai, embedding_bge)
                 → enrich_status='embedded' (검색 가능)
```

> **임베딩 input은 enrich 결과 4관점 텍스트만**. `rv_products.body_text` 원본은 noise 많아 사용하지 않음. 정형값(brand/price/category)은 임베딩 아닌 정형 필터로 검색에 활용 — `docs/MULTIMODAL_ENRICH.md` 의 설계 의도.

---

## 3. DB 스키마 요약

### 3.1 주요 테이블 (사용 빈도 순)

| 테이블 | PK | 핵심 컬럼 | 역할 | 도입 마이그레이션 |
|--|--|--|--|--|
| `advertisers` | id | name, host_type, shop_url, shop_key, license_key, cafe24_*, notes, detail_anchor_start/end | 광고주(쇼핑몰) 마스터 | 002, 004(셀렉터 단일컬럼; 005에서 다중화), 006(anchor) |
| `advertiser_selectors` | id | advertiser_id, role(name/detail/price), selector, priority | 광고주별 셀렉터 N개를 priority로 시도 | 005 |
| `advertiser_image_blocks` | id | advertiser_id, pattern, enabled | 다운로드 단계에서 URL 부분문자열 매칭 차단 | 008 |
| `rv_products` | id | advertiser_id+product_code unique, product_name, price, sale_price, product_url, image_url, raw_payload(jsonb), body_text, html_path, image_dir, scrape_status, enrich_method, ... | 광고주에서 동기화한 원본 상품 | 002, 003 (enrich 컬럼) |
| `rv_product_images` | id | advertiser_id+product_code+src_url unique, local_filename | 스크랩한 src URL 인덱스(공통 배너 탐지) | 014 |
| `rv_product_excluded_images` | id | advertiser_id+product_code+image_filename unique, reason | 다음 enrich에서 skip할 이미지 | 007 |
| `product_enriched` | rv_product_id (FK to rv_products) | category, category_confidence, brand, brand_tier, price_tier, target_gender, target_age_min/max, origin_country, manufacturer, category_attributes(jsonb), compatible_products(jsonb), set_components(jsonb), tags(jsonb), desc_situation/material/style/persona, image_types(jsonb), enrich_method, model_used, input_tokens, output_tokens, cost_usd, cache_read/creation_input_tokens, duration_ms, validation_score, validation_warnings/errors, trap_detected, refined_fields, enrich_status('extracted'\|'validated'\|'refined'\|'embedded'\|'failed'), enrich_version, raw_response | LLM 멀티모달 추출 결과 (PDF v1.0 케이스 04 패턴 — 공통속성 + 카테고리별 JSONB + 다관점 설명문) | 009, 010(단계 컬럼), 011(비용 컬럼) |
| `product_descriptions` | id (unique by rv_product_id+perspective) | perspective(situation/material/style/persona), description, embedding_openai vector(1536), embedding_bge vector(1024), model | 4관점 임베딩. 검색용. HNSW cosine 인덱스 모델별 따로. | 010, 012(dual embedding rename) |
| `product_sync_status` | (advertiser_id, sync_type) | status(IN_PROGRESS/COMPLETED/FAILED), last_page, total_count, saved_count, error_message | 동기화 재개·진행률 | 002 |
| `search_logs` | id | query, semantic_query, llm_used, llm_provider, backend, advertiser_id, result_count, keyword_filters(text[]), keyword_filter_relaxed, llm_duration_ms, embed_duration_ms, total_duration_ms, llm_input_tokens, llm_output_tokens, llm_cost_usd, llm_parsed_json(jsonb), created_at | 검색 로깅(품질 분석·비용 추적) | 013(기본 테이블 + llm cost), 015(parsed_json), 016(타이밍 컬럼) |
| `search_log_results` | id | log_id(FK), rank, rv_product_id, product_name, image_url, distance, perspective, description, tags(jsonb), desc_persona | 검색 결과 스냅샷(랭킹 평가용) | 016 |
| **(legacy)** `products` | id | sku, name, brand, category, gender, season, style, color, price, sale_price, stock, description, tags, deleted_at | v0 데모 상품 — `/recommend` 진입점에서만 사용 | 001 |
| **(legacy)** `product_embeddings` | product_id(FK) | embedding vector(1024), embedded_text, model | v0 데모 임베딩 | 001 |
| ~~`outbox_events`~~ | — | — | 코드 grep 시 producer/consumer 없음 — **VIT-4 에서 제거** (017_drop_outbox_events.sql). | 001(생성) → 017(제거) |

### 3.2 핵심 관계 (요약 ERD, 텍스트)

```
advertisers ─┬── advertiser_selectors (1:N, role+priority)
             ├── advertiser_image_blocks (1:N)
             ├── rv_products (1:N) ──┬── product_enriched (1:1)
             │                        ├── product_descriptions (1:N, perspective)
             │                        ├── rv_product_images (1:N, src_url)
             │                        └── rv_product_excluded_images (1:N)
             └── product_sync_status (1:1 per sync_type)

search_logs ── search_log_results (1:N)

(legacy) products ── product_embeddings (1:1)
```

### 3.3 마이그레이션 현황

- `migrations/001`~`017.sql` 까지 순차 적용. **backend 부팅 시 Flyway 가 `classpath:db/migration` (=루트 `migrations/`) 을 모두 idempotent 하게 적용** (VIT-4 도입). Spring Boot autoconfigure 가 시작 시 자동 실행 — 신규 SQL 만 추가하고 backend 재시작하면 반영.
  - 설정: `application.yml` → `spring.flyway` (`baseline-on-migrate: true`, `validate-on-migrate: false`, `sql-migration-prefix: ""`, `sql-migration-separator: "_"`).
  - 빌드: `backend/build.gradle` 의 `processResources` 가 루트 `migrations/*.sql` 을 `build/resources/main/db/migration/` 으로 복사.
  - 모든 마이그레이션은 `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` 등 멱등 SQL — 기존 환경 baselining 시 재실행해도 안전.
- docker-compose 의 `./migrations:/docker-entrypoint-initdb.d` 마운트는 **fresh `docker compose up` 시 첫 스키마 부트스트랩 편의용** (backend 없이 embedder/psql 만 띄울 때 빈 DB 방지). Flyway 가 source of truth — 운영 적용은 backend boot 으로.
- `setup_search_logs.py`, `migrate_search_logs_v2.py` 는 VIT-4 에서 삭제됨. 정의는 `013_search_logs_llm_cost.sql` (기본 + llm cost) 와 `016_search_logs.sql` (타이밍 + `search_log_results`) 에 흡수.
- `db/recommend.dump` 는 v0 데모 데이터(10k 의류) 복원용.
- `idx_*` 인덱스는 hnsw(vector_cosine_ops) + gin(jsonb) + btree(상태·외래키 등) 골고루 깔려 있음. price 인덱스는 v0 `products`에는 있으나 `rv_products`에는 없음 — 가격 필터 쿼리 EXPLAIN 한 번 떠봐야 함(→ child issue 후보).

---

## 4. API 표면

### 4.1 Backend (Spring Boot :8090)

| Method | Path | 책임 | 인증 |
|--|--|--|--|
| GET | `/healthz` | 헬스체크 | 없음 |
| POST | `/recommend` | (legacy) LLM 파싱+벡터+rerank 통합 추천 | 없음 |
| POST | `/search` | (legacy) 임베딩+SQL만, LLM 없음 | 없음 |
| POST | `/enrich` | embedder `/scrape` 로 단순 프록시 (URL→OCR/scrape 결과) | 없음 |
| GET | `/advertisers` | 광고주 목록 | 없음 |
| POST | `/advertisers` | 광고주 생성 | 없음 |
| PUT | `/advertisers/{id}` | 광고주 수정 | 없음 |
| DELETE | `/advertisers/{id}` | 광고주 삭제 | 없음 |
| POST | `/advertisers/{id}/sync` | 메이크샵 OpenAPI 동기 호출 → rv_products UPSERT | 없음 |
| GET | `/advertisers/{id}/sync-status` | product_sync_status 조회 | 없음 |
| GET/POST/PATCH/DELETE | `/advertisers/{id}/selectors[/{sid}]` | 광고주 셀렉터 CRUD | 없음 |
| GET/POST/DELETE | `/advertisers/{id}/image-blocks[/{bid}]` | URL 차단 패턴 CRUD | 없음 |
| GET | `/advertisers/{id}/products` | rv_products 페이지네이션 + enriched/embedded set | 없음 |
| POST | `/advertisers/{id}/products/{code}/scrape` | 단일 상품 스크랩 | 없음 |
| POST | `/advertisers/{id}/products/batch-fetch` | 백그라운드 배치 스크랩 (mode=page\|api, concurrency, intervalMs) | 없음 |
| GET | `/advertisers/{id}/products/batch-fetch/status` | 배치 진행률 | 없음 |
| POST | `/advertisers/{id}/products/batch-fetch/cancel` | 배치 취소 | 없음 |
| POST | `/advertisers/{id}/products/{code}/enrich/ocr` | 단일 OCR | 없음 |
| POST | `/advertisers/{id}/products/{code}/enrich/vlm` | 단일 VLM | 없음 |
| POST | `/advertisers/{id}/products/{code}/enrich/llm?model=&imageLimit=&force=` | 단일 LLM enrich | 없음 |
| POST | `/advertisers/{id}/products/batch-enrich?model=haiku\|sonnet&count=&concurrency=&embedBackend=bge` | 백그라운드 LLM 배치 enrich | 없음 |
| GET/POST | `/advertisers/{id}/products/batch-enrich/status\|cancel` | 진행률/취소 | 없음 |
| GET (정적) | `/advertisers/{id}/products/{code}/images/{sha1}.jpg` | 광고주 상품 이미지 직접 서빙 (frontend 검색 카드/모달이 그대로 참조) | 없음 |

### 4.2 Embedder (FastAPI :8001) — 사실상 frontend의 직접 의존

`embedder/server.py`가 7개 라우터 include:

- `embed_router`(server.py): `POST /embed` (bge-m3 1024d), `GET /healthz`
- `scrape_router`(scrape.py): `POST /scrape` — 광고주 페이지 HTML 가져와 셀렉터/앵커로 본문 슬라이스 + 이미지 다운로드(sha1 dedupe, image_blocks 적용)
- `enrich_router`(enrich.py): `POST /enrich/ocr`, `POST /enrich/vlm`, `POST /enrich/llm`, qwen-vl 등
- `enrich_llm_router`(enrich_api.py): 단일/배치 LLM enrich, image-blocks 관리, common-images 탐지
- `search_rv_router`(search_rv.py): `POST /search-rv`, `POST /search-rv-llm`, `GET /products-rv/{id}/detail`, `GET /advertisers-rv`, `GET /advertisers-rv/{id}/stats`, `POST /embed/openai`
- `products_list_router`/`logs_router`(products_list.py): `GET /products-enriched`, `/products-enriched/count`, `/products-enriched/categories`, `/products-enriched/endorsers`, `POST /products-enriched/{id}/endorser-tag`, `GET /search-logs`, `/search-logs/stats`, `/search-logs/{id}/results`
- `rv_products_router`(rv_products_api.py): rv_products 페이지네이션/통계 (admin/products.html용)

### 4.3 인증/인가 모델

> 2026-05-20 VIT-3 적용: backend/embedder/admin 전 endpoint에 Bearer 게이트 + 광고주 secret 컬럼 암호화 + admin 비밀번호 로그인을 도입했다. 아래는 현재 모델.

- **Bearer 토큰 게이트**: backend(`com.example.recommend.security.BearerAuthFilter`) 와 embedder(`embedder/auth.py`) 모두 `Authorization: Bearer ${APP_API_TOKEN}` 을 요구. 예외는 (1) `/healthz`, (2) backend의 `/admin/login`(자격증명 → 토큰 발급), (3) `OPTIONS` preflight, (4) backend의 광고주 이미지 정적 path(`/advertisers/{id}/products/{code}/images/*` — 프론트 `<img>` 태그가 직접 박는 URL이라 토큰화 미적용; signed URL 도입은 follow-up).
  - 토큰 미설정 시 동작: 기본 `APP_ALLOW_UNAUTHENTICATED=true` (dev) → 통과 + WARN 로그. `application-prod.yml`은 이 값을 강제 `false` 로 덮어써 보호 endpoint가 503 응답.
  - Spring → embedder 사이드카 호출 시 `app.embedder.api-token` 를 첨부 (기본은 `${APP_API_TOKEN}` 공유; 별도 분리하려면 `EMBEDDER_API_TOKEN` override).
- **광고주 키 암호화**: `advertisers.shop_key / license_key / cafe24_access_token` 컬럼을 application-layer AES-GCM 으로 암호화 (`SecretCipher`, prefix `enc:v1:`). REST 응답(`GET /advertisers`, `GET /advertisers/{id}`)에서는 마지막 4글자만 노출되도록 마스킹 (`SecretCipher.mask`). 동기화 서비스(`ProductSyncService`)는 레포에서 복호화된 plain text 값을 받아 메이크샵 OpenAPI에 사용. 환경변수 `APP_SECRET_KEY`(16/24/32-byte base64) 미설정 시 plaintext passthrough — dev 폴백, 프로덕션 필수.
- **CORS 화이트리스트**: embedder는 `CORS_ORIGINS` env CSV 사용, 미설정 시 dev 폴백(loopback/사설 대역). backend `CorsConfig`는 `app.security.cors-origins` 프로퍼티(`APP_CORS_ORIGINS` env) 사용. `application-prod.yml` 은 기본을 빈 값으로 둬서 운영 시 반드시 도메인 명시.
- **응답 stacktrace 차단**: `application-prod.yml` 에서 `server.error.include-{message,stacktrace,exception}` 을 모두 `never`/`false` 로 변경.
- **admin 패널 로그인**: `admin/login.html` 의 첫 단계가 ID/PW 폼 → `/admin/login` 호출 → 발급받은 토큰을 `localStorage.aira_token` 으로 저장. 이후 모든 admin 페이지가 `auth.js` 를 가장 먼저 로드해 `window.fetch` 를 패치, backend/embedder 호스트로 가는 요청에 자동으로 Bearer 첨부. 401 응답 시 토큰 제거 후 로그인으로 강제 리다이렉트. 자격증명은 `ADMIN_USERNAME` / `ADMIN_PASSWORD` env. (광고주 선택 화면이었던 기존 `aira_adv` 흐름은 토큰 발급 후 두 번째 단계로 유지.)
- **남은 항목 (별도 child issue)**:
  - frontend(Next.js) 가 embedder `/search-rv*` 를 직접 호출 — 운영에 안전하게 띄우려면 Next.js route handler 서버측 프록시로 Bearer 를 주입해 토큰 노출을 막아야 함.
  - 광고주 상품 이미지 path 의 signed URL.
  - 기존 DB 의 plaintext `license_key/shop_key/cafe24_*_token` 행을 일괄 재암호화하는 마이그레이션 스크립트.

---

## 5. 운영 상태 — 로컬 실행 검증

### 5.1 현재 떠있는 프로세스 (2026-05-20 분석 시점)

`netstat -an | grep LISTEN` 결과 다음 4개 포트가 살아있음:

| 포트 | 서비스 | 검증 |
|--|--|--|
| 5433 | Postgres (pgvector) | docker-compose recommend-pg 컨테이너 |
| 8001 | embedder FastAPI | `GET /healthz` → `{"ok":true,"model":"BAAI/bge-m3","dim":1024}` |
| 8090 | Spring backend | `GET /healthz` → `{"ok":true}` |
| 3000 | Next.js | `/` 응답 (HTML) |

### 5.2 검색 파이프라인 end-to-end 검증

```
POST http://localhost:8001/search-rv  body={"query":"미니멀 14k 귀걸이","advertiser_id":1,"backend":"bge","k":3}
→ 200 OK
→ backend=bge, dim=1024, advertiser_id=1, k=3
→ debug_sql + items[] 정상 반환 (luvreparis 광고주에 enriched 상품 1056건 존재)
```

`GET /advertisers-rv` 결과:
- id=1 luvreparis (makeshop) — enriched 1,056개 ✅
- id=2 루브르파리 (makeshop) — enriched 0
- id=3 luvreparis [LLM테스트] (makeshop) — enriched 0

### 5.3 로컬 실행 방법 (검증된 순서)

```bash
# 1) Postgres (pgvector) 띄우기 — 첫 기동 시 migrations/*.sql 자동 적용
cd D:/WORKSPACE/상품추천/recommand-solution
docker compose up -d
# (선택) 기존 dump 복원:
docker exec -i recommend-pg pg_restore -U app -d recommend --clean --if-exists < db/recommend.dump

# 2) Python embedder
python -m venv .venv
.venv/Scripts/pip install -U pip
.venv/Scripts/pip install -r requirements.txt
# .env 또는 환경변수:
#   DB_DSN=postgresql://app:app@localhost:5433/recommend
#   GROQ_API_KEY=...   (또는 OPENAI_API_KEY=...)
#   RECOMMAND_DATA_DIR=D:/recommand-data
.venv/Scripts/python -m uvicorn embedder.server:app --host 127.0.0.1 --port 8001

# 3) Spring backend
cd backend
./gradlew bootRun
# (또는 IDE에서 RecommendApiApplication 실행)
# 환경변수: LLM_API_KEY, GROQ_API_KEY, LLM_MODEL, LLM_BASE_URL 등
# application.yml: server.port=8090, datasource :5433, embedder base-url http://127.0.0.1:8001

# 4) Frontend
cd frontend
npm install
npm run dev   # next dev -H 0.0.0.0
# .env.local (선택): NEXT_PUBLIC_API_BASE=http://localhost:8090
#                    NEXT_PUBLIC_EMBEDDER_BASE=/api/embedder
```

### 5.4 환경 변수 한눈에 보기

> 보안 관련 env (VIT-3 도입): 프로덕션은 모두 필수.
>
> | Var | 사용처 | 권장값 |
> |--|--|--|
> | `APP_API_TOKEN` | backend + embedder Bearer 검증, Spring→embedder 호출, admin 로그인 응답 토큰 | 32+ chars 랜덤 (env 또는 secret store) |
> | `APP_ALLOW_UNAUTHENTICATED` | true 면 토큰 미설정 시 인증 우회 (dev 전용) | prod: 미설정 / `application-prod.yml` 가 false 로 덮어씀 |
> | `APP_SECRET_KEY` | 광고주 secret 컬럼 AES-GCM 키 | 32-byte base64 (`openssl rand -base64 32`) |
> | `ADMIN_USERNAME`, `ADMIN_PASSWORD` | admin 패널 로그인 자격증명 | 운영 비밀번호 (외부 secret store) |
> | `APP_CORS_ORIGINS` | backend CORS 화이트리스트 (CSV) | `https://admin.example.com,https://www.example.com` |
> | `CORS_ORIGINS` | embedder CORS 화이트리스트 (CSV) | 동일 패턴 |
> | `EMBEDDER_API_TOKEN` | Spring→embedder 호출 시 token override (선택) | 미설정이면 `APP_API_TOKEN` 공유 |
> | `SPRING_PROFILES_ACTIVE=prod` | prod profile 활성화 (stacktrace off, CORS empty default, fail-closed) | prod 필수 |



| Var | 사용처 | 기본값/예시 |
|--|--|--|
| `DB_DSN` | embedder/Python 스크립트 | `postgresql://app:app@localhost:5433/recommend` |
| `RECOMMAND_DATA_DIR` | embedder + backend 정적파일 | `D:/recommand-data` (Win) / `/var/lib/recommand/data` (Linux 권장) |
| `EMBED_MODEL` | embedder server.py | `BAAI/bge-m3` |
| `OPENAI_API_KEY` | embedder OpenAI 임베딩 + embed_descriptions.py | (필수, 미설정 시 OpenAI 백엔드 차단) |
| `OPENAI_EMBED_MODEL` | embedder search_rv.py | `text-embedding-3-small` |
| `OPENAI_CHAT_MODEL` | embedder LLM 파싱 (openai provider 선택 시) | `gpt-4o-mini` |
| `GROQ_API_KEY` | embedder LLM 파싱(groq), Spring LlmClient (alias `LLM_API_KEY`) | (필수, LLM 검색 사용 시) |
| `GROQ_BASE`, `GROQ_MODEL` | embedder Groq client | `https://api.groq.com/openai/v1`, `llama-3.3-70b-versatile` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Spring LlmClient | `https://api.groq.com/openai/v1`, GROQ_API_KEY fallback, `llama-3.3-70b-versatile` |
| `CLAUDE_BIN`, `CLAUDE_MODEL`, `VALIDATE_MODEL` | embedder/enrich_claude.py 등 | `claude`, `sonnet`, `haiku` |
| `ENRICH_MAX_IMAGES`, `ENRICH_IMG_MAX_DIM`, `ENRICH_IMG_QUALITY` | embedder enrich 이미지 처리 | 30, 1280, 82 |
| `NEXT_PUBLIC_API_BASE` | frontend → backend | `http://localhost:8090` (page.tsx는 `http://192.168.101.27:8090` 하드코딩 폴백) |
| `NEXT_PUBLIC_EMBEDDER_BASE` | frontend → embedder | `/api/embedder` (next.config rewrite로 `:8001`) |

### 5.5 시드/마이그레이션 절차

- **첫 기동**: `docker compose up -d` 시 entrypoint 가 `migrations/*.sql` 일괄 적용 (편의용 부트스트랩). 이후 backend 가 부팅하면 Flyway 가 동일 SQL 을 다시 idempotent 하게 검증·적용하고 `flyway_schema_history` 에 기록.
- **데이터 시드**: 두 가지 경로.
  - (a) `db/recommend.dump` 복원 — v0 의류 10k 상품 + 임베딩 즉시.
  - (b) `scripts/seed_products.py` + `scripts/embed_products.py` — Makefile의 `make seed` / `make embed`로 실행.
- **추가 마이그레이션**: `migrations/NNN_*.sql` 추가 → backend 재시작만 하면 Flyway autoconfigure 가 새 파일을 적용 (VIT-4 도입). 마이그레이션 SQL 은 모두 멱등(`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) 으로 작성.

---

## 6. 리스크 & 기술부채

### 6.1 보안 (P0) — 2026-05-20 VIT-3 적용

- ✅ backend + embedder 전 endpoint Bearer 토큰 게이트 (BearerAuthFilter / `embedder/auth.py`). `/healthz`, `/admin/login`, 광고주 이미지 정적 path 만 예외.
- ✅ `advertisers.shop_key/license_key/cafe24_access_token` 컬럼을 AES-GCM 으로 application-layer 암호화 (`SecretCipher`, prefix `enc:v1:`); API 응답은 마지막 4자 마스킹.
- ✅ embedder CORS 가 `CORS_ORIGINS` env-driven CSV 화이트리스트. backend CORS 도 `APP_CORS_ORIGINS` env 사용; `application-prod.yml` 기본은 빈 값(운영 시 명시 필수).
- ✅ `application-prod.yml` 에서 `server.error.include-{message,stacktrace,exception}` 을 모두 차단.
- ✅ admin 패널: ID/PW 로그인 → 토큰 발급 → 모든 admin fetch에 Bearer 자동 첨부 + 401 시 강제 재로그인.
- 남은 follow-up:
  - frontend(Next.js) 가 직접 embedder `/search-rv*` 호출 — Next.js route handler 서버측 프록시로 토큰 주입 (브라우저에 토큰 노출 방지). **child issue 필요**.
  - 광고주 상품 이미지 path 의 signed URL.
  - 기존 DB plaintext `license_key/shop_key/cafe24_*_token` 행 재암호화 마이그레이션 스크립트.

### 6.2 스키마/마이그레이션 (P0) — 2026-05-20 VIT-4 적용
- ✅ `search_logs` / `search_log_results` 가 `migrations/013_search_logs_llm_cost.sql` (기본 + llm cost) 와 `migrations/016_search_logs.sql` (타이밍 + 결과 스냅샷) 로 흡수. 루트의 `setup_search_logs.py` / `migrate_search_logs_v2.py` 삭제.
- ✅ Flyway (Spring Boot autoconfigure) 도입. backend 부팅 시 `classpath:db/migration/*.sql` 자동 적용. `application.yml` 의 `spring.flyway` 블록 + `backend/build.gradle` 의 `processResources` 가 루트 `migrations/` 를 jar 안으로 복사.
- ✅ `outbox_events` 미사용 확인 후 `migrations/017_drop_outbox_events.sql` 로 제거.
- 남은 follow-up:
  - `rv_products` 가격 필터(`COALESCE(sale_price, price)`) 인덱스 없음 — 광고주당 수만 건 시 풀스캔 가능. **child issue 필요**.

### 6.3 코드/디버그 잔존물 (P1)
- 루트의 `check_gender.py`, `tmp_token_count.py` — 일회성 스크립트가 그대로 커밋됨. (`migrate_search_logs_v2.py`, `setup_search_logs.py` 는 VIT-4 에서 제거.)
- `embedder.log`, `embedder_err.log` 가 repo 안에 — `.gitignore` 누락.
- `embedder/search_rv.py` 에 `print(f"[PRICE_DEBUG] ...")` `# TODO: remove` 가 남음.
- `frontend/app/page.tsx` 의 `NEXT_PUBLIC_API_BASE ?? "http://192.168.101.27:8090"` 등 사설 IP 하드코딩.
- `admin/dashboard.html` 의 `BACKEND = SEARCH = 'http://localhost:8001'` (의도 확인 필요; backend 통계 endpoint도 있는데 둘 다 embedder 가리킴).

### 6.4 운영/배포 (P1)
- `RECOMMAND_DATA_DIR` 가 `D:/recommand-data` 윈도우 절대경로에 의존. Linux 배포 시 별도 systemd 환경변수 + 마운트 디스크 설계 필요.
- 배치 스크랩/enrich가 백엔드 프로세스 안에서 데몬 스레드로 돌고 있음 (`ProductEnrichController.batchEnrich`). 프로세스 재시작 시 진행상태 손실 가능. 작업 큐(Postgres advisory lock + outbox 또는 별도 워커) 도입 검토.
- 검색 path 의 LLM 비용·duration 은 search_logs에 기록되지만 backend 의 `/recommend` 호출은 로깅되지 않음(legacy 라 의도적일 수 있음).

### 6.5 성능 (P2)
- `search_rv_llm` 의 4관점 평균 거리 계산은 상품당 4행 distance를 모두 계산 후 AVG — 인덱스가 HNSW라 첫 후보 모으는데는 빠르지만 `AVG`로 정렬하는 ranked CTE는 풀 매칭에 의존. 광고주별 10만+ 상품 가면 cost 측정 필요.
- v0 `RecommendController.search` 는 `e.embedding <=> ?` 를 SELECT/ORDER BY 양쪽에 둬서 vec 파라미터 2번 바인딩 — okhttp+JdbcTemplate path 검증됨.
- embedder가 LLM 호출에 동기 requests 사용 (`search_rv._llm_parse_query`). 동시 검색 트래픽 늘면 워커 수 튜닝 필요.

### 6.6 데이터 일관성 (P2)
- `product_descriptions` 는 perspective 4행이 항상 있어야 하지만, embed_descriptions가 부분 실패할 경우 일부 perspective만 존재할 수 있음. NULL handling은 검색 SQL에 `embedding_bge IS NOT NULL` 가드 있음.
- `compatible_products` / `set_components` jsonb는 enrich가 trap을 회피한 경우에만 채워지는 패턴. 검색에는 미사용 — 추후 cross-sell 기획 시 활용 여지.

---

## 7. 다음 단계 추천 (우선순위 3~5)

### P0-1. 인증 + 보안 하드닝 (1~2주)
- backend/embedder/admin 모든 API에 토큰 기반(JWT 또는 단순 shared secret) 게이트 추가.
- `advertisers.license_key/shop_key` 암호화 + 마스킹 응답.
- production profile에서 `error.include-stacktrace=never`, CORS 화이트리스트.
- 이유: 운영 노출 시 1일 내 LLM 비용 폭주 + 광고주 키 유출 위험.

### P0-2. 스키마/마이그레이션 정상화 — ✅ VIT-4 (2026-05-20) 완료
- `search_logs` / `search_log_results` 정의를 `migrations/013` (기본) + `016` (타이밍·스냅샷) 로 흡수, 루트 ad-hoc 스크립트 삭제.
- Spring Boot Flyway 도입 — backend 부팅 시 자동 적용.
- `outbox_events` 사용 없음 확인 → `017_drop_outbox_events.sql` 로 제거.

### P1-3. 코드 잔존물 정리 (1~2일)
- 루트의 `check_gender.py`, `tmp_token_count.py`, `embedder.log`, `embedder_err.log` 제거 + `.gitignore` 보강.
- frontend/admin의 localhost/192.168.x.x 하드코딩을 env로 일원화. `admin/`의 BACKEND/SEARCH 분리 의도 확인.
- `[PRICE_DEBUG]` 등 TODO/디버그 print 제거.

### P1-4. 배치 워커 분리 (1~2주)
- backend 프로세스 안의 daemon thread 배치를 별도 워커(또는 Python embedder-worker)로 분리. Postgres advisory lock 또는 간단한 `job_queue` 테이블로 재시작 시 재개 보장.
- 이유: 현재 batch-enrich가 backend 재시작에 취약하고, scaling 도 불가.

### P2-5. `/recommend` (legacy) 정리 결정 (1주)
- 데모/스모크용으로 유지할지 v1 라인에 흡수할지 결정. 유지한다면 v0 데이터(`products`/`product_embeddings`) 갱신 정책 정립. 흡수한다면 backend `/recommend` deprecate + smoke 테스트만 다른 엔드포인트로 옮기기.

---

## 8. 부록 — 자주 참조하는 파일/경로

- 검색 SQL의 진실: `embedder/search_rv.py:551-589` (`_run`), `:454-512` (필터 빌드), legacy는 `backend/src/main/java/com/example/recommend/search/ProductSearchRepository.java`.
- LLM 파싱 프롬프트(가장 자주 튜닝됨): `embedder/search_rv.py:264-356` (`_LLM_PARSE_PROMPT`).
- 멀티모달 enrich 통합 호출: `embedder/enrich_claude.py` (846 라인 — 가장 큰 파일, 별도 분석 권장).
- 배치 스크랩/enrich 컨트롤 흐름: `backend/src/main/java/com/example/recommend/sync/ProductEnrichService.java` (534 라인) + `ProductEnrichController.java`.
- 광고주 메이크샵 동기화: `backend/.../sync/ProductSyncService.java`, `MakeshopClient.java`.
- 사용자 검색 UI: `frontend/app/page.tsx` (검색 메인), `frontend/app/search-rv/page.tsx` (운영자 검색).
- 운영자 광고주/상품 관리: `frontend/app/sync/page.tsx`, `frontend/app/sync/[advertiserId]/products/page.tsx` (1,470라인 — 가장 큰 페이지).
- 스키마: `migrations/001.sql`~`015.sql` + 루트 `setup_search_logs.py`, `migrate_search_logs_v2.py`.
- 운영 가이드: `docs/MULTIMODAL_ENRICH.md` (이 시스템에 대한 가장 핵심적인 기존 문서).
