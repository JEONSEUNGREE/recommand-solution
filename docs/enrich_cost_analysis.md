# 상품 분석(Enrich) 비용 분석

> 기준: 2026-05-19 / 실측 1,056건 평균

---

## 처리 흐름 (2 Pass)

```
[상품 1개 처리]

Pass 1 — 이미지 필터 (이미지 > 4장일 때만)
  모델: claude haiku
  목적: 광고/배너 이미지 미리 걸러내기
  입력: 이미지 파일 경로 목록 (최대 30장)
  출력: ["sha1a", "sha1b", ...] — 본 상품 관련 이미지 sha1 배열
  타임아웃: 180초

      ↓ 통과 이미지만

Pass 2 — 본 분석
  모델: haiku (기본) | sonnet (CLAUDE_MODEL 환경변수로 변경)
  목적: 카테고리 / 공통속성 / 태그 / 4관점 설명문 / 이미지 분류
  입력: system prompt + JSON schema + user prompt + 이미지 첨부
  출력: JSON 1덩어리 (~8,920 토큰 avg)
  타임아웃: 300초
```

---

## 프롬프트 구성 (Pass 2)

| 구성 요소 | 내용 | 크기 |
|-----------|------|------|
| **시스템 프롬프트** | 분석 규칙 7가지 + 마케팅 표현 절대 금지 + 이미지 처리 규칙 | ~2,700자 |
| **JSON 스키마** | category / product / common_attributes / category_attributes / tags / descriptions / images / trap_detection | ~3,500자 |
| **유저 프롬프트** | 상품명, URL, 플랫폼, 본문 텍스트, 이미지 파일 경로 목록 | ~500자 + 본문 |
| **이미지 첨부** | 리사이즈된 이미지 (claude CLI 파일경로 → 자동 첨부) | 아래 참조 |

### 추출 항목 (Pass 2 출력)

1. `product` — 정제된 상품명, 가격, 옵션
2. `common_attributes` — 브랜드, 브랜드티어(럭셔리/프리미엄/중가/저가), 성별, 연령, 원산지
3. `category_attributes` — 카테고리별 동적 속성 (주얼리: metal/karat/stones 등, 의류: fit/fabric 등)
4. `tags` — situation / mood / feature 3분류, 15~30개, 각 confidence
5. `descriptions` — 4관점 설명문 (situation / material / style / persona), 각 30~80자
6. `images` — 전달된 모든 이미지에 image_type / is_main_product / used_for_attributes 분류
7. `set_components` / `related_products` / `trap_detection` — 트랩 회피용

---

## 이미지 처리 설정

| 설정 | 값 | 환경변수 |
|------|----|---------|
| 장변 최대 | **1,280 px** | `ENRICH_IMG_MAX_DIM` |
| JPEG 품질 | **82** | `ENRICH_IMG_QUALITY` |
| 상품당 최대 이미지 수 | **30장** | `ENRICH_MAX_IMAGES` |

### 이미지 1장당 토큰 계산식

```
tokens = (width × height) / 750   ← Claude 공식 계산식
```

| 이미지 크기 | 토큰 수 | 비고 |
|------------|--------|------|
| 1,280 × 1,280 | **~2,185** | 코드 설정 최대 |
| 1,280 × 800 | **~1,365** | 가로형 상품컷 |
| 800 × 800 | **~853** | 정방형 일반 |
| 640 × 640 | **~547** | |
| 400 × 400 | **~213** | 썸네일급 |

**예시**: 상품 이미지 10장 × 800px 기준 → 이미지 토큰만 약 **8,500**

---

## 실측 토큰 / 비용 (DB 1,056건 평균)

| 항목 | 평균 | 비고 |
|------|------|------|
| `input_tokens` | **281** | 상품별 텍스트 (상품명·URL·본문) |
| `output_tokens` | **8,920** | JSON 출력 |
| `cache_creation_input_tokens` | **76,301** | 이미지 + 시스템 프롬프트 첫 캐시 기록 |
| `cache_read_input_tokens` | **103,596** | 캐시 히트 (재사용) |
| **`cost_usd` 평균** | **$0.157** | Pass 1 + Pass 2 합산 |
| `cost_usd` 중앙값 | $0.149 | |
| `cost_usd` 최소 | $0.072 | |
| `cost_usd` 최대 | $0.390 | |
| **총 1,056건 합계** | **$165.36** | |

> `input_tokens 281`은 상품별 텍스트만. 이미지 토큰은 첫 전송 시 `cache_creation`, 재전송(같은 이미지) 시 `cache_read`로 처리됨.

---

## 비용 이론값 vs 실측값

### Claude Haiku 요금표

| 토큰 유형 | 요금 |
|-----------|------|
| Input | $0.80 / MTok |
| Cache write | $1.00 / MTok |
| Cache read | $0.08 / MTok |
| Output | $4.00 / MTok |

### 평균 기준 이론값 계산

| 토큰 유형 | 토큰 수 | 단가 | 비용 |
|-----------|--------|------|------|
| input | 281 | $0.80/MTok | $0.000225 |
| cache_creation | 76,301 | $1.00/MTok | $0.0763 |
| cache_read | 103,596 | $0.08/MTok | $0.0083 |
| output | 8,920 | $4.00/MTok | $0.0357 |
| **합계** | | | **~$0.121** |

> 실측 $0.157과 차이는 Pass 1(filter call) 비용 + 일부 sonnet 호출 포함.

### 규모별 비용 추정

| 상품 수 | 예상 비용 (haiku 기준 $0.12) | 실측 기준 ($0.157) |
|--------|---------------------------|-----------------|
| 100건 | $12 | $16 |
| 1,000건 | $120 | $157 |
| 5,000건 | $600 | $785 |
| 10,000건 | $1,200 | $1,570 |

---

## 검색(search_rv) 비용

| 검색 유형 | LLM 호출 | 비용 |
|-----------|----------|------|
| `/search-rv` | 없음 — pgvector 유사도만 | **$0** |
| `/search-rv-llm` | Groq (Llama 3.3 70B) 1회 | **현재 무료 티어** |

- 임베딩 모델(bge-m3 1024-dim): **로컬 실행 → $0**
- 벡터 DB 조회(pgvector): **DB 쿼리 비용만**

---

## 관련 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CLAUDE_MODEL` | `sonnet` | Pass 2 모델 (haiku 추천) |
| `ENRICH_MAX_IMAGES` | `30` | 상품당 최대 이미지 수 |
| `ENRICH_IMG_MAX_DIM` | `1280` | 이미지 장변 최대 px |
| `ENRICH_IMG_QUALITY` | `82` | JPEG 압축 품질 |
| `ENRICH_CALL_TIMEOUT` | `300` | Pass 2 타임아웃 (초) |
| `ENRICH_FILTER_TIMEOUT` | `180` | Pass 1 타임아웃 (초) |
