# 추천 채팅 고도화 계획

> 작성일: 2026-05-20  
> 현재 파이프라인: `자연어 입력 → LLM Parse (Groq Llama-3.3-70b) → BGE 임베딩 → 벡터 검색 → 결과 반환`

---

## 1. 현재 구조 파악

### LLM Parse가 추출하는 것 (search_rv.py `_llm_parse_query`)

| 필드 | 설명 | 예시 |
|------|------|------|
| `semantic_query` | 임베딩용 정제 표현 | "봄 데이트 14k 귀걸이" |
| `target_gender` | 구매자 성별 | "여성"\|"남성"\|"공용"\|null |
| `target_age_min/max` | 연령대 | 20, 29 |
| `category_contains` | 카테고리 ILIKE | "귀걸이" |
| `perspective_preference` | 검색 관점 | "situation"\|"material"\|"style"\|"persona" |
| `keyword_filters` | 필수 속성 키워드 | ["손예진"] |
| `notes` | 가격 표현 등 메모 | "가격 5만원대" |

### 현재 미처리 영역

1. **가격 필터** — `notes`에 텍스트로만 남기고 실제 price_min/max 필터 없음
2. **부적절/무관 쿼리** — 욕설·상품 무관 질문 무방비 통과
3. **추상 표현** — "요즘 핫한 거", "엄마 선물" 등 → 임베딩만 믿는 상황

---

## 2. 욕설 / 부적절 표현 필터

### 2-1. 3단계 필터링 전략

```
입력 → [레이어 1: 클라이언트 정규식] → [레이어 2: LLM 분류] → [레이어 3: 서버 차단]
```

**레이어 1 — 클라이언트 정규식 (즉각 차단, 서버 요청 없음)**

```js
// chat.html send() 함수 상단에 추가
const PROFANITY_PATTERN = /씨발|시발|병신|개새|지랄|존나|fuck|shit|섹스|야동/i;
const OFF_TOPIC_PATTERN = /주식|코인|날씨|로또|주가|환율|bitcoin/i;

if (PROFANITY_PATTERN.test(txt)) {
  appendAIMsg('상품 검색에 적합하지 않은 표현이 포함되어 있어요. 다시 입력해주세요.');
  return;
}
if (OFF_TOPIC_PATTERN.test(txt) && txt.length < 20) {
  appendAIMsg('저는 상품 추천 전용 AI예요. 찾으시는 상품을 알려주세요!');
  return;
}
```

**레이어 2 — LLM Parse 스키마 확장 (search_rv.py)**

현재 LLM 프롬프트에 다음 필드 추가:

```json
{
  "semantic_query": "...",
  "filters": { ... },
  "notes": "...",
  "content_check": {
    "is_shopping_query": true,
    "has_profanity": false,
    "block_reason": null
  }
}
```

- `is_shopping_query: false` → 상품 추천 거부 + 안내 메시지 반환
- `has_profanity: true` → 요청 차단
- `block_reason` → 사용자에게 보여줄 안내 이유

**레이어 3 — 서버 차단 (embedder/search_rv.py)**

```python
# LLM parse 결과에서 content_check 확인
check = parsed.get("content_check", {})
if check.get("has_profanity") or not check.get("is_shopping_query", True):
    return {
        "blocked": True,
        "block_reason": check.get("block_reason", "상품 검색과 관련없는 질문입니다."),
        "items": [],
        "result_count": 0,
    }
```

### 2-2. 클라이언트 차단 메시지 처리 (chat.html)

```js
if (data.blocked) {
  appendAIMsg(data.block_reason || '해당 질문은 처리할 수 없습니다.', '');
  return;
}
```

---

## 3. 가격 표현 정규화

### 3-1. 가격 표현 패턴 분류

| 표현 | 해석 | 변환 방식 |
|------|------|-----------|
| "저렴한", "싼", "절약" | 하위 25% 이하 | `price_max = P25(카테고리)` |
| "가성비", "합리적인 가격" | 하위 25~50% | `price_max = P50(카테고리)` |
| "중간 가격대", "적당한" | 35~65% 구간 | `price_min = P35, price_max = P65` |
| "비싼", "고급", "럭셔리" | 상위 25% 이상 | `price_min = P75(카테고리)` |
| "프리미엄", "명품급" | 상위 15% 이상 | `price_min = P85(카테고리)` |
| "5만원대" | ±10% 버퍼 | `price_min=45000, price_max=55000` |
| "5만원 이하" | 상한 | `price_max=50000` |
| "5만원 이상" | 하한 | `price_min=50000` |
| "10~15만원" | 범위 | `price_min=100000, price_max=150000` |

### 3-2. 구현 방법

**① DB 가격 백분위 캐시 (1시간마다 갱신)**

```sql
-- 광고주 × 카테고리별 가격 백분위
SELECT
  advertiser_id,
  category,
  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY sale_price) AS p25,
  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY sale_price) AS p50,
  PERCENTILE_CONT(0.65) WITHIN GROUP (ORDER BY sale_price) AS p65,
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY sale_price) AS p75,
  PERCENTILE_CONT(0.85) WITHIN GROUP (ORDER BY sale_price) AS p85
FROM rv_products
WHERE sale_price > 0 AND advertiser_id = %s
GROUP BY advertiser_id, category;
```

전체 카테고리 백분위도 별도 보관 (카테고리 필터 없을 때 fallback).

**② LLM Parse 스키마에 가격 필드 추가**

```json
"price_filter": {
  "price_min": null,
  "price_max": null,
  "price_tier": "budget|mid|premium|luxury|null",
  "price_literal": "5만원대"
}
```

- `price_tier`가 있으면 → 서버에서 백분위 테이블 참조해 min/max 계산
- `price_literal`이 있으면 → 정규식으로 파싱해 직접 min/max 변환
- 둘 다 있으면 `price_literal` 우선

**③ 벡터 검색 WHERE 절에 price 조건 추가 (search_rv.py)**

```python
# 기존 filters 처리 뒤에 추가
price_min = parsed_filters.get("price_filter", {}).get("price_min")
price_max = parsed_filters.get("price_filter", {}).get("price_max")

# price_tier → 백분위 조회
price_tier = parsed_filters.get("price_filter", {}).get("price_tier")
if price_tier and not price_min and not price_max:
    stats = get_price_percentiles(advertiser_id, category_filter)
    TIER_MAP = {
        "budget":  (None, stats["p25"]),
        "mid":     (stats["p25"], stats["p65"]),
        "premium": (stats["p75"], None),
        "luxury":  (stats["p85"], None),
    }
    price_min, price_max = TIER_MAP.get(price_tier, (None, None))

if price_min:
    conditions.append("rp.sale_price >= %s")
    params.append(price_min)
if price_max:
    conditions.append("rp.sale_price <= %s")
    params.append(price_max)
```

### 3-3. LLM 프롬프트 예시 추가

```
쿼리: "5만원대 귀걸이"
→ {"price_filter":{"price_min":45000,"price_max":55000,"price_tier":null,"price_literal":"5만원대"},...}

쿼리: "저렴한 귀걸이"
→ {"price_filter":{"price_min":null,"price_max":null,"price_tier":"budget","price_literal":"저렴한"},...}

쿼리: "럭셔리한 느낌의 목걸이"
→ {"price_filter":{"price_min":null,"price_max":null,"price_tier":"luxury","price_literal":"럭셔리"},...}
```

---

## 4. 추상적 표현 처리

### 4-1. 현재 문제 케이스

| 표현 | 현재 처리 | 이상적 처리 |
|------|-----------|-------------|
| "요즘 핫한 거" | semantic_query 그대로 임베딩 | keyword_filters에 "인기","트렌드" 추가 |
| "엄마 선물" | 성별 null | target_gender=여성, age_min=45 추론 |
| "20대 감성" | semantic_query 포함 | target_age_min=20, target_age_max=29 추출 |
| "인싸템" | 그대로 임베딩 | "트렌디한 인기 아이템"으로 rewrite |
| "그거 말고" | 의미 불명 | 이전 검색 컨텍스트 필요 (세션 연속성) |
| "무드등 같은 느낌" | 다른 카테고리 비유 | style perspective + 분위기 키워드 추출 |

### 4-2. LLM 프롬프트 보강 (추가할 예시)

```
쿼리: "요즘 핫한 귀걸이"
→ {"semantic_query":"트렌디한 인기 귀걸이","filters":{...,"keyword_filters":["인기","트렌드"]},"notes":"트렌드 키워드 추가"}

쿼리: "엄마 선물로 살 화장품"
→ {"semantic_query":"중년 여성 선물용 화장품","filters":{"target_gender":"여성","target_age_min":45,...},"notes":"엄마=45세 이상 여성 추론"}

쿼리: "인싸들이 좋아하는 액세서리"
→ {"semantic_query":"트렌디하고 인기 많은 액세서리","filters":{...,"keyword_filters":["인기","트렌드"]},"notes":"인싸=트렌드 시그널"}

쿼리: "화이트 무드 느낌의 원피스"
→ {"semantic_query":"화이트 청결하고 밝은 무드 원피스","filters":{...,"perspective_preference":"style"},"notes":"색감/분위기 묘사 → style"}
```

### 4-3. 세션 컨텍스트 (멀티턴 대화) — 중기 과제

"그거 말고", "더 저렴한 거", "비슷한 거" 같은 표현은 이전 검색 결과를 알아야 해석 가능.

**구현 방향:**
```
채팅 → 각 turn마다 session_id 부여
        ↓
search_logs.session_id 컬럼 추가
        ↓
이전 turn의 semantic_query, 결과 상품 목록 → 다음 LLM Parse에 context로 주입
```

LLM 프롬프트에 `conversation_context` 필드 추가:
```json
{
  "previous_query": "귀걸이 추천",
  "previous_category": "귀걸이",
  "previous_results_count": 10
}
```

---

## 5. 구현 우선순위

| 우선순위 | 항목 | 난이도 | 기대 효과 |
|----------|------|--------|-----------|
| ★★★ | 가격 리터럴 파싱 ("5만원대" → range) | 낮음 | 즉각 검색 정확도 향상 |
| ★★★ | 클라이언트 욕설 정규식 필터 | 낮음 | 기본 안전성 |
| ★★☆ | LLM content_check 필드 추가 | 중간 | LLM 토큰 약간 증가, 정확한 분류 |
| ★★☆ | 가격 티어 → DB 백분위 변환 | 중간 | "저렴한", "비싼" 처리 |
| ★★☆ | LLM 프롬프트 예시 보강 (추상 표현) | 낮음 | "엄마선물", "인싸템" 처리 |
| ★☆☆ | 세션 컨텍스트 멀티턴 | 높음 | "그거 말고" 류 처리 |

---

## 6. 가격 티어 기준표 (예시 — 현재 광고주 데이터 기반)

> 실제 값은 `rv_products`의 `sale_price` 분포로 계산. 아래는 플레이스홀더.

| 티어 | 표현 | 현재 광고주 기준 (추산) |
|------|------|------------------------|
| budget | 저렴한, 싼, 가성비 | ~ P25 |
| mid | 중간 가격, 적당한 | P25 ~ P65 |
| premium | 비싼, 고급, 럭셔리 | P75 ~ |
| luxury | 프리미엄, 명품급 | P85 ~ |

실제 백분위 조회 API:
```
GET /advertisers/{adv_id}/price-stats?category=귀걸이
→ { "p25": 29000, "p50": 49000, "p65": 69000, "p75": 89000, "p85": 120000 }
```
