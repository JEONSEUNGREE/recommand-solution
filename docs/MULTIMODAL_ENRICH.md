# 멀티모달 Enrich 파이프라인 — 팀 공유 가이드

> 광고주 상품 페이지 → 멀티모달 LLM 추출 → 구조화된 DB + 벡터 임베딩.
> 참조 문서: `대화형_상품_추천_시스템_LLM_추출_전략_v1.0.md`, `멀티모달_실험_보고서.pdf`

---

## 1. 큰 그림

```
[광고주 페이지]
    │
    │  Stage 1 · 수집 (Spring Boot — sync 모듈)
    │  · makeshop OpenAPI / Cafe24 / Playwright 어댑터
    │  · 결과: rv_products(DB) + 정적 파일(D:/recommand-data)
    ▼
┌─────────────────────────────────────────────────────────┐
│ rv_products (DB)                                        │
│ + D:/recommand-data/advertisers/{id}/products/{code}/   │
│     source.html, extracted.json, images/*.jpg           │
└─────────────────────────────────────────────────────────┘
    │
    │  Stage 2 · 속성 추출 (Python — embedder/*)
    │  1) enrich_claude.py  — 통합 추출 (Sonnet, 1 call)
    │  2) validate_claude.py — 검증 (Haiku, 선택)
    │  3) refine_claude.py   — 보정 (선택)
    │  4) embed_descriptions.py — 임베딩 (OpenAI 1536d)
    ▼
┌─────────────────────────────────────────────────────────┐
│ product_enriched (구조화)  +  product_descriptions (벡터)│
└─────────────────────────────────────────────────────────┘
    │
    │  Stage 3 · 대화형 추천 (백엔드 + 프론트)
    ▼
[유저 자연어 쿼리 → 하이브리드 검색 → 큐레이션 응답]
```

PDF 권장은 Stage 2를 3번 호출(Haiku 분류 → Sonnet 추출 → Haiku 검증)로 쪼개는 것. 우리는 **1번 통합 호출**로 시작해서 적재하고, 검증/보정은 같은 DB row를 누적 업데이트하는 구조로 단계별 분리해두었다 (마이그레이션 010).

---

## 2. 정적 파일 — 디렉토리 보관 (DB 외부)

이미지/HTML 같은 큰 바이너리·텍스트는 DB에 안 넣고 **파일시스템**에 저장한다. 경로는 환경변수 `RECOMMAND_DATA_DIR`로 추상화.

### 2.1 기본 경로

| 환경 | 기본값 | 설정 방법 |
|------|--------|----------|
| 로컬 (Windows) | `D:/recommand-data` | 디폴트 |
| 로컬 (mac/linux) | `~/recommand-data` 또는 임의 SSD 경로 | `export RECOMMAND_DATA_DIR=/path/to/data` |
| 서버 (Linux) | `/var/lib/recommand/data` 권장 | systemd Environment 또는 docker volume |

### 2.2 디렉토리 구조 (스크랩 결과 + 디버그)

```
$RECOMMAND_DATA_DIR/
├─ advertisers/
│   └─ {advertiser_id}/
│       └─ products/
│           └─ {product_code}/
│               ├─ source.html       ← 광고주 페이지 원본 HTML
│               ├─ extracted.json    ← scrape 파서 결과 (텍스트 + 이미지 메타)
│               └─ images/
│                   └─ {sha1}.jpg    ← 다운로드된 이미지 (sha1로 dedupe)
│
├─ enrich_debug/                      ← enrich_claude.py 디버그 (gitignore)
│   ├─ payload_{ts}.txt              ← claude한테 보낸 prompt 전체
│   ├─ stdout_{ts}.txt               ← claude envelope JSON 응답
│   └─ stderr_{ts}.txt               ← stderr
│
└─ logs/                              ← 임의 로그 (gitignore)
```

### 2.3 파일 vs DB 매핑

`rv_products` 테이블에 정적파일 경로가 컬럼으로 저장됨:
- `rv_products.html_path` → `…/products/{code}/source.html`
- `rv_products.image_dir` → `…/products/{code}/images/`

코드에서 읽을 때:
```java
// backend (Spring Boot)
String dataDir = System.getenv().getOrDefault("RECOMMAND_DATA_DIR", "D:/recommand-data");
```
```python
# python
DATA_DIR = os.environ.get("RECOMMAND_DATA_DIR", "D:/recommand-data")
```

### 2.4 서버 배포 시 주의

- **운영 환경**: 상품 수만 개 × 이미지 30장 = 수십 GB. **별도 마운트 디스크** 권장 (SSD).
- **백업**: DB만 백업해도 추출 결과(`product_enriched`)는 남지만, **이미지 원본은 다시 받기 어려움** (광고주 CDN이 비활성화될 수 있음). 정적 파일도 백업 대상에 포함.
- **권한**: 백엔드 + embedder 둘 다 동일 경로에 r/w 필요. 도커 컴포즈로 동일 volume 마운트.
- **CDN 노출 금지**: 광고주 이미지를 우리 서버로 다시 서빙하지 말 것 (저작권). 백엔드는 enrich 처리에만 사용.

---

## 3. Stage 2 스크립트 4종 (embedder/)

### 3.1 enrich_claude.py — 1차 통합 추출 (필수)

```bash
# 단일 상품
python -m embedder.enrich_claude <rv_product_id>

# 옵션
--no-db              # DB 적재 생략 (stdout만)
--keep-workdir       # 리사이즈 임시 디렉토리 유지
--limit N            # 이미지 N장만 보내기 (기본 30)
--image-sha1 <hex>   # 특정 이미지 1장만 (디버깅)
```

**환경변수**:
- `CLAUDE_BIN=claude` (기본)
- `CLAUDE_MODEL=sonnet` (haiku/opus 등)
- `ENRICH_MAX_IMAGES=30`
- `ENRICH_IMG_MAX_DIM=1280` (리사이즈 한 변 max px)
- `ENRICH_IMG_QUALITY=82` (JPEG 품질)
- `ENRICH_DEBUG_DIR=D:/recommand-data/enrich_debug`

**출력**: `product_enriched` 테이블에 INSERT (rv_product_id PK upsert).

**추출 항목** (한 번에):
1. `category` (name + path + confidence)
2. `product` 본체 (정제된 name, price, options)
3. `common_attributes` (brand/tier/gender/age/origin/manufacturer)
4. `category_attributes` (JSONB — 의류/주얼리/화장품 도메인별 자유 키)
5. `set_components` (SET 상품일 때 분해)
6. `related_products` (이미지에 보이는 다른 상품 — 본 상품 속성 오염 방지)
7. `tags` (situation/mood/feature, 15~30개)
8. `descriptions` (situation/material/style/persona 4관점)
9. `images` (모든 sha1에 대해 image_type + content_description + used_for_attributes)
10. `ignored_observations` (오염 방지 격리 — 사용하지 않은 정보 기록)
11. `trap_detection` (is_set_or_combo, is_trap)

### 3.2 validate_claude.py — 2차 검증 (선택)

```bash
python -m embedder.validate_claude <rv_product_id>
python -m embedder.validate_claude --batch --status extracted --limit 50
```

Haiku로 가볍게 trust_score 계산 + 트랩 재확인. 결과를 `product_enriched.validation_*` 컬럼에 저장하고 `enrich_status='validated'`로 전환.

환경변수: `VALIDATE_MODEL=haiku`

### 3.3 refine_claude.py — 보정 (선택, skeleton만)

검증에서 `errors`가 나온 필드만 다시 추출해서 보강. 필요할 때 작성.

### 3.4 embed_descriptions.py — 임베딩 (필수)

```bash
python -m embedder.embed_descriptions <rv_product_id>
python -m embedder.embed_descriptions --batch --status validated --limit 50
```

`product_enriched`의 descriptions 4관점만 임베딩 → `product_descriptions(perspective, embedding(1536))` 적재.

환경변수: `OPENAI_API_KEY=...`, `EMBED_MODEL=text-embedding-3-small`

**중요**: 임베딩 input은 **descriptions 4관점 텍스트만**. `rv_products`의 원본 body_text는 noise(다른 상품 추천·배송안내 등) 많아서 사용하지 않음. 정형 정보(brand/price/category)는 임베딩 대신 **정형 필터**로 검색에 활용.

---

## 4. DB 스키마 (관련 부분만)

```
rv_products (Stage 1 산출)
    └─ id, advertiser_id, product_code, product_name, price, html_path, image_dir, ...

product_enriched (Stage 2 산출 — 1행/상품)
    ├─ rv_product_id (FK, PK)
    ├─ category, category_confidence
    ├─ brand, brand_tier, price_tier, target_gender, target_age_min/max, ...
    ├─ category_attributes JSONB
    ├─ tags JSONB
    ├─ desc_situation/material/style/persona TEXT
    ├─ image_types JSONB (sha1 → type + content_description)
    ├─ compatible_products JSONB (related_products)
    ├─ set_components JSONB
    ├─ raw_response TEXT (디버깅용 원본 응답)
    │
    ├─ enrich_status TEXT  -- extracted → validated → embedded
    ├─ enrich_version INT
    │
    ├─ (검증) validation_score REAL, validation_warnings/errors JSONB, trap_detected JSONB,
    │         validated_at, validated_by
    │
    └─ (보정) refined_fields JSONB, refined_at

product_descriptions (Stage 2 산출 — 4행/상품)
    ├─ rv_product_id (FK)
    ├─ perspective ('situation'|'material'|'style'|'persona')
    ├─ description TEXT
    ├─ embedding vector(1536)   -- HNSW 인덱스
    └─ model TEXT
```

마이그레이션:
- `migrations/009_product_enriched.sql` — product_enriched 본체
- `migrations/010_product_enriched_steps.sql` — 단계별 컬럼 + product_descriptions

---

## 5. 실행 흐름 예시 (한 상품 끝까지)

```bash
# 0) 사전 (이미 끝남): scrape로 rv_products + 정적파일 생성됨

# 1) 1차 추출 (Sonnet 통합 호출, ~30~150초/상품)
python -m embedder.enrich_claude 1

# → product_enriched row 생성, enrich_status='extracted'
# → D:/recommand-data/enrich_debug/{ts}_* 파일 남음

# 2) 검증 (Haiku 가볍게, ~5초/상품) — 선택
python -m embedder.validate_claude 1

# → validation_score, trap_detected 등 갱신
# → enrich_status='validated'

# 3) 임베딩 (OpenAI text-embedding-3-small, ~2초/상품)
export OPENAI_API_KEY=sk-...
python -m embedder.embed_descriptions 1

# → product_descriptions 4행 적재
# → enrich_status='embedded'

# 4) 배치 처리 (광고주 전체)
python -m embedder.enrich_claude --batch ...    # (배치 모드는 추가 작성 필요)
python -m embedder.validate_claude --batch --status extracted --limit 100
python -m embedder.embed_descriptions --batch --status validated --limit 100
```

---

## 6. 운영 / 배포 체크리스트

### 6.1 의존성

**Python (embedder)**:
- `psycopg[binary]`, `Pillow`, `openai` (임베딩용)
- Claude CLI 설치 + 로그인 (`claude --version`, OAuth via `claude /login`)

**시스템**:
- PostgreSQL 16 + pgvector
- 정적 파일 디렉토리 (충분한 SSD)
- (선택) OpenAI API key — 임베딩용

### 6.2 환경변수 (`.env` 또는 systemd Environment)

```ini
# DB
DB_DSN=postgresql://app:app@localhost:5433/recommend

# 정적 파일
RECOMMAND_DATA_DIR=/var/lib/recommand/data

# Claude
CLAUDE_BIN=claude
CLAUDE_MODEL=sonnet
VALIDATE_MODEL=haiku

# 임베딩
OPENAI_API_KEY=sk-...
EMBED_MODEL=text-embedding-3-small

# 이미지 처리
ENRICH_MAX_IMAGES=30
ENRICH_IMG_MAX_DIM=1280
ENRICH_IMG_QUALITY=82
```

### 6.3 서버 배포 시 디렉토리 권한

```bash
# 예: /var/lib/recommand/data 를 backend·embedder 둘 다 r/w
sudo mkdir -p /var/lib/recommand/data
sudo chown -R app:app /var/lib/recommand/data
sudo chmod 750 /var/lib/recommand/data

# systemd unit 예
[Service]
Environment="RECOMMAND_DATA_DIR=/var/lib/recommand/data"
Environment="DB_DSN=postgresql://app:..."
ReadWritePaths=/var/lib/recommand/data
```

### 6.4 gitignore 확인

정적 파일은 git에 절대 들어가면 안 됨:
```
# .gitignore
recommand-data/
**/source.html
**/extracted.json
**/images/
**/enrich_debug/
```
(현재 데이터가 D:/recommand-data 외부에 있어서 자동 제외되지만, 향후 repo 안에 두지 말 것.)

---

## 7. 검증된 1개 케이스 (참고)

상품: `rv_product_id=1`, code=2280473 (루브르파리 지토 레이어드 SET — 14K 귀걸이+목걸이 SET).

- 입력: 1장(컬러 옵션 인포그래픽) + 페이지 메타 텍스트
- 결과: 17 + 23 + 4(JSON 카테고리·태그·설명문) — PDF 케이스 04 수준
- 트랩 회피 검증: ✅
  - related_products: EA2193(다른 추천 상품) → `shown_in_image_but_not_this`
  - ignored_observations 3건: EA2193/EA2166/NA0632 단품가 분리
  - trap_detection.is_set_or_combo: true (SET 정확히 인식)
- 비용: $0.35 (Sonnet 4.6), 시간 156초 (1장 + 큰 schema)

> 이미지 늘리거나 PDF 권장 하이브리드(Haiku+Sonnet)로 가면 비용 ~$0.10/상품, 시간 ~15초로 떨어짐.

---

## 8. 참고 문서

- `대화형_상품_추천_시스템_LLM_추출_전략_v1.0.md` — 프롬프트 5종, 모델 선택, 비용
- `멀티모달_실험_보고서.pdf` — 케이스 1~4 실증 결과 (특히 케이스 04 임상 데이터 추출)
- `대화형_상품_추천_시스템_아키텍처_v1.pdf` — 전체 아키텍처
- `docs/recommend_enrich_prompt_test_plan.md` — Claude 이미지 감사 결과를 기반으로 최종 enrich를 요청하는 프롬프트와 오염 방지 테스트 기준
