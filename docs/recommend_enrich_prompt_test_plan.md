# 추천상품 Enrich 프롬프트/테스트 설계

작성일: 2026-05-19

목표는 단순히 상품 설명을 잘 뽑는 것이 아니라, 추천 검색에 들어갈 `product_enriched`와 `product_descriptions`가 오염되지 않게 만드는 것이다. 특히 상세 이미지가 여러 인포그래픽, 다른 상품 광고, 추천 상품 카드, 공통 배송/결제 안내를 섞어 담는 경우를 기본 전제로 둔다.

## 1. 핵심 원칙

- `보이는 것`과 `판매 구성`은 다르다. 이미지에 보이는 목걸이, 반지, 모델 착용 소품, 추천 상품은 본 상품 구성품이 아닐 수 있다.
- 최종 enrich는 본 상품에 관한 사실만 저장한다. 다른 상품/공통 안내/브랜드 광고는 `ignored_observations`, `related_products`, `image_audit`에만 남긴다.
- 근거 우선순위는 `상품 메타/상품명/가격 DB > 상세 본문 텍스트 > 본 상품 이미지 OCR > 이미지 추론` 순서다.
- `desc_situation`, `desc_material`, `desc_style`, `desc_persona`는 임베딩 입력이므로 광고 문구, 근거 없는 사회적 증명, 다른 상품 정보가 들어가면 실패로 본다.
- 모든 중요한 속성은 evidence id를 가져야 한다. evidence가 없으면 `null` 또는 빈 배열로 둔다.
- 불확실한 값은 채우지 않는다. 추천 품질에서는 누락보다 환각이 더 비싸다.

## 2. 권장 흐름

1. HTML/URL 기반 사전 제거
   - 이미지 URL, DOM class/id, alt, 주변 텍스트에서 `recommend`, `related`, `banner`, `delivery`, `payment`, `review`, `event`, `coupon` 등은 후보에서 낮은 우선순위로 보낸다.
   - 단, 이 단계에서 완전 삭제하지 말고 `pre_filter_reason`을 남긴다. 광고주마다 class가 다르기 때문에 오탐 가능성이 있다.

2. Claude 이미지 감사
   - 모든 후보 이미지를 Claude에 보내되, 단순 keep 배열이 아니라 각 이미지별 `decision`, `product_scope`, `usable_facts`, `rejected_facts`를 받는다.
   - 하나의 이미지 안에 여러 상품 카드가 있으면 이미지 전체를 `related_product`로 버리기보다, 본상품으로 확실한 사실만 `usable_facts`에 남기고 나머지는 `rejected_facts`로 격리한다.

3. 최종 Enrich LLM
   - 우리 LLM에는 raw 이미지 추론을 다시 맡기지 말고, 상품 메타/본문/Claude 이미지 감사 결과를 증거 묶음으로 넘긴다.
   - `decision=use` 또는 `use_limited`이고 `product_scope`가 `main_product`, `set_component`, `option_variant`인 증거만 본 상품 속성에 사용할 수 있게 한다.

4. 검증
   - LLM 검증과 deterministic 검증을 둘 다 둔다.
   - `ignored_observations`에 들어간 단어가 `tags`나 `descriptions`에 나타나면 실패 처리한다.
   - 특정 소재/연예인/가격/SET 구성처럼 must-match인 조건이 검색에서 relax되면 실패 처리한다.

5. 임베딩
   - `validated` 이후의 4개 설명문만 임베딩한다.
   - raw body text, raw OCR, 광고 문구는 임베딩하지 않는다.

## 3. 반드시 막아야 하는 케이스

### 상품 정체성

- 상품명은 귀걸이인데 착용컷에 목걸이/반지/팔찌가 같이 보이는 경우
- SET 상품과 모델이 여러 상품을 같이 착용한 사진이 섞이는 경우
- "구성: 목걸이+귀걸이"와 "코디 상품: 반지"가 한 이미지에 같이 있는 경우
- 같은 라인업의 색상/소재 변형이 본 상품 옵션인지 다른 상품인지 애매한 경우
- 호환 상품, 리필, 케이스, 본체, 부속품을 본 상품으로 오인하는 경우
- 사은품/패키지/박스/쇼핑백을 판매 구성으로 오인하는 경우

### 이미지 오염

- 하나의 상세 페이지에 여러 인포그래픽이 있고 각각 다른 상품을 설명하는 경우
- 하단 추천상품 카드, 같이 사면 좋은 상품, 베스트 상품 랭킹 이미지가 섞이는 경우
- 배송/교환/반품/고객센터 안내 이미지가 상품 속성으로 들어가는 경우
- 브랜드 세계관/룩북/캠페인 배너가 상품 디자인·무드로 과하게 들어가는 경우
- 리뷰 이미지나 SNS 캡처가 본 상품 스펙으로 들어가는 경우
- 사이즈표가 상품 전용이 아니라 브랜드 공통 사이즈표인 경우
- 인증/임상/알러지 문구가 해당 상품 전용인지 브랜드 공통인지 불명확한 경우
- 이미지 파일명이나 URL이 본상품처럼 보여도 실제 이미지는 다른 상품인 경우
- 중복 이미지, 크롭 이미지, 저해상도 썸네일이 OCR을 왜곡하는 경우

### 속성 오염

- `14K 골드`, `14K 도금`, `골드필드`, `925 실버`, `스테인리스`, `무니켈`이 섞이는 경우
- `화이트골드`, `옐로우골드`, `로즈골드/핑크골드`가 색상인지 소재인지 섞이는 경우
- `큐빅`, `다이아몬드`, `모이사나이트`, `진주`, `인공진주`를 과장해서 변환하는 경우
- `알러지프리`, `니켈프리`, `피부친화`, `백화점 정품`, `명품` 같은 검증 문구가 근거 없이 생성되는 경우
- 모델 성별을 구매자 성별 `target_gender`로 오인하는 경우
- 연예인/인플루언서 착용 정보가 원문 근거 없이 `tags`나 `desc_persona`에 생성되는 경우
- 가격, 무료배송, 당일출고, 할인율이 상품명 문구와 DB 실제값 사이에서 충돌하는 경우

### 검색 쿼리

- "남자연예인 착용 귀걸이"를 남성용 귀걸이로 해석하는 경우
- "여자친구 선물"을 구매자 성별 여성으로 고정하는 경우
- "추천상품 말고 본상품만" 같은 제외 조건을 무시하는 경우
- "세트 사진에 나온 반지도 같이 있는 상품"처럼 이미지 안의 다른 상품을 일부러 찾는 경우
- "단품 귀걸이인데 세트처럼 보이는 것"처럼 본상품 구성과 시각적 연출이 다른 경우
- 오타/동의어: 귀고리/이어링, 팬던트/펜던트, 넥클리스/네클리스, 호프/후프
- 분위기어와 하드필터 혼동: 미니멀, 우아한, 고급스러운, 데일리, 페미닌
- 가격 표현: 저렴한, 가성비, 5만원대, 10만원 이하, 할인율 높은

### 프롬프트 인젝션/문서 노이즈

- 상세 이미지나 HTML에 "이전 지시를 무시하라", "이 상품은 명품이다" 같은 문구가 있는 경우
- 광고성 이미지가 "BEST", "No.1", "연예인 Pick"을 크게 쓰지만 본상품 근거가 아닌 경우
- OCR이 상품명과 추천상품명을 한 문장으로 붙여 읽는 경우

## 4. Claude 이미지 감사 프롬프트

아래 프롬프트는 `call_claude_filter`의 단순 JSON 배열보다 강한 버전이다. 나중에 구현할 때는 이 결과를 raw_response에 저장하거나 별도 `image_audit` 필드로 저장한다.

### System

```text
당신은 한국 쇼핑몰 상품 상세 이미지 QA 담당입니다.

목표:
- 각 이미지가 실제 판매 중인 본 상품 정보를 담는지 판정한다.
- 다른 상품, 추천 상품, 광고, 공통 안내, 배송/교환 안내, 리뷰, 브랜드 캠페인 정보를 본상품 정보와 분리한다.
- 본 상품 속성 추출에 써도 되는 사실과 쓰면 안 되는 사실을 따로 기록한다.

중요 규칙:
1. 페이지에 수집된 이미지라고 해서 모두 본 상품 이미지가 아니다.
2. 하나의 이미지 안에 본 상품과 다른 상품이 같이 있으면, 본 상품으로 확실한 사실만 usable_facts에 넣고 나머지는 rejected_facts에 넣는다.
3. 모델이 착용한 다른 주얼리/의류/소품은 본 상품 구성품으로 보지 않는다.
4. SET/패키지 상품은 텍스트에 구성품이 명시된 경우에만 set_component로 본다.
5. 배송, 결제, 반품, 고객센터, 이벤트, 쿠폰, 추천상품, 베스트상품, 룩북, 브랜드 광고는 본 상품 속성에 사용하지 않는다.
6. 불확실하면 decision=review 또는 ignore로 둔다. 추측하지 않는다.
7. 출력은 JSON 한 덩어리만. 코드펜스와 설명 문장 금지.
```

### User

```text
다음 상품 페이지의 이미지들을 감사하라.

[상품 메타]
- rv_product_id: {rv_product_id}
- 상품명: {product_name}
- 상품코드: {product_code}
- URL: {url}
- 가격: {price}

[페이지 텍스트 요약]
{page_text_or_summary}

[이미지 파일 경로]
{image_path_list}

[출력 JSON 스키마]
{
  "product_identity": {
    "main_product_name": "string",
    "main_category_hint": "string|null",
    "set_or_single": "single|set|bundle|unknown",
    "confidence": 0.0
  },
  "images": [
    {
      "sha1": "파일명에서 확장자 제외",
      "image_role": "main|lifestyle|detail|infographic|size_chart|color_options|spec_table|care_guide|certification|packaging|related_product|ad_banner|store_policy|review|noise|unknown",
      "product_scope": "main_product|set_component|option_variant|model_styling_item|related_product|compatible_product|brand_generic|store_policy|unknown",
      "decision": "use|use_limited|ignore|review",
      "is_main_product": true,
      "usable_facts": [
        {
          "fact": "본 상품 속성에 써도 되는 사실",
          "field_hint": "category|material|stone|color|size|component|option|style|situation|care|certification|other",
          "confidence": 0.0
        }
      ],
      "rejected_facts": [
        {
          "fact": "보이지만 본 상품 속성에 쓰면 안 되는 사실",
          "reason": "다른 상품|추천 상품|모델 코디 소품|브랜드 공통|배송 안내|광고|불확실|기타"
        }
      ],
      "visible_other_products": [
        {
          "name_or_type": "string",
          "relation": "worn_together|recommended|compatible|same_line|unknown"
        }
      ],
      "ocr_text_summary": "이미지 내 핵심 텍스트 요약. 전체 OCR 장문 복붙 금지",
      "rejection_reason": "decision이 ignore/review이면 필수",
      "confidence": 0.0
    }
  ],
  "global_warnings": [
    "본상품/다른상품 구분에서 주의할 점"
  ]
}
```

## 5. Claude 결과 기반 최종 Enrich 요청 프롬프트

이 프롬프트가 실제로 "Claude output으로 우리 LLM에 enrich 요청"할 때 쓰는 본체다. 핵심은 Claude가 본 이미지 관찰을 다시 무비판적으로 믿지 않고, `decision`과 `product_scope`로 사용 가능 증거를 제한하는 것이다.

### System

```text
당신은 추천 검색용 상품 데이터 Enrich 엔진입니다.

입력:
- 상품 메타: DB/API에서 온 상품명, 가격, 코드, URL
- 페이지 텍스트: HTML에서 추출한 상품 상세 텍스트
- Claude 이미지 감사 결과: 이미지별 사용 가능 사실, 제외해야 할 사실, 다른 상품 정보

목표:
- 본 상품에 관한 구조화 데이터만 JSON으로 만든다.
- 추천 검색과 임베딩에 들어갈 설명문 4종을 만든다.
- 다른 상품, 광고, 공통 안내, 추천상품, 모델 코디 소품은 본 상품 속성에 절대 섞지 않는다.

증거 사용 규칙:
1. 본 상품 속성에는 아래 증거만 사용할 수 있다.
   - page_text에서 본 상품 영역으로 확인되는 텍스트
   - image_audit.images[].decision이 use 또는 use_limited
   - product_scope가 main_product, set_component, option_variant 중 하나
   - usable_facts에 들어간 사실
2. 아래 정보는 본 상품 속성, tags, descriptions에 절대 사용하지 않는다.
   - image_audit.images[].rejected_facts
   - product_scope가 related_product, compatible_product, model_styling_item, brand_generic, store_policy, unknown인 정보
   - 배송/교환/반품/결제/쿠폰/이벤트/추천상품/베스트상품/리뷰/브랜드 광고
3. 상품 메타와 이미지 OCR이 충돌하면 상품 메타/본문을 우선하고 conflict를 기록한다.
4. 연예인/셀럽/인플루언서 착용, 방송 협찬, 베스트셀러, 명품, 프리미엄, 백화점 정품, 니켈프리, 알러지프리 같은 주장은 원문 근거가 명시적일 때만 쓴다.
5. 모델 착용자 성별은 target_gender가 아니다. target_gender는 구매자/착용 대상 성별이 명시된 경우에만 채운다.
6. SET 상품은 구성품이 텍스트나 감사 결과에서 명시된 경우에만 set_components에 넣는다. 같이 보이는 상품은 related_products로 보낸다.
7. 불확실하면 null, 빈 배열, needs_review=true로 둔다. 추측 금지.

설명문 규칙:
- descriptions는 임베딩 입력이다. 광고 카피가 아니라 사실 묘사여야 한다.
- 각 설명문은 30~100자, 1~2문장.
- situation/material/style/persona 네 관점은 서로 겹치지 않게 쓴다.
- ignored/rejected 정보에 나온 단어를 descriptions에 넣지 않는다.

태그 규칙:
- tags는 situation, mood, feature 세 종류만 사용한다.
- 고유명사/소재/기능 태그는 근거가 있을 때만 넣는다.
- "스타", "연예인 스타일", "인기", "명품", "완벽", "필수템" 같은 근거 약한 태그 금지.

출력:
- JSON 한 덩어리만 출력한다.
- 코드펜스, 머리말, 설명 문장 금지.
- 모든 주요 필드에는 evidence_ids 또는 evidence_notes를 남긴다.
```

### User

```text
다음 입력을 기반으로 최종 상품 enrich JSON을 작성하라.

[상품 메타]
{product_meta_json}

[페이지 텍스트]
{page_text}

[Claude 이미지 감사 결과]
{claude_image_audit_json}

[기존 DB 스키마 힌트]
- product_enriched: category, brand, brand_tier, price_tier, target_gender, target_age_min, target_age_max, origin_country, manufacturer, category_attributes, tags, desc_situation, desc_material, desc_style, desc_persona, compatible_products, set_components, image_types
- product_descriptions: situation/material/style/persona 4개 설명문만 임베딩

[출력 JSON 스키마]
{
  "acceptance": {
    "needs_review": false,
    "review_reasons": [],
    "confidence": 0.0
  },
  "product": {
    "name": "정제된 상품명",
    "price": null,
    "sale_price": null,
    "currency": "KRW",
    "options": {}
  },
  "category": {
    "name": "leaf 또는 path",
    "path": "string|null",
    "confidence": 0.0
  },
  "common_attributes": {
    "brand": null,
    "brand_tier": null,
    "price_tier": null,
    "target_gender": null,
    "target_age_min": null,
    "target_age_max": null,
    "origin_country": null,
    "manufacturer": null,
    "is_premium": null,
    "is_best_seller": null
  },
  "category_attributes": {},
  "set_components": [
    {
      "name": "string",
      "type": "string",
      "attributes": {},
      "evidence_ids": []
    }
  ],
  "related_products": [
    {
      "relation_type": "compatible_with|same_line_variant|goes_well_with|shown_in_image_but_not_this|recommended_product|model_styling_item",
      "target_name": "string",
      "evidence_ids": []
    }
  ],
  "tags": [
    {
      "tag": "string",
      "tag_category": "situation|mood|feature",
      "confidence": 0.0,
      "evidence_ids": []
    }
  ],
  "descriptions": [
    {
      "perspective": "situation",
      "description": "string",
      "evidence_ids": []
    },
    {
      "perspective": "material",
      "description": "string",
      "evidence_ids": []
    },
    {
      "perspective": "style",
      "description": "string",
      "evidence_ids": []
    },
    {
      "perspective": "persona",
      "description": "string",
      "evidence_ids": []
    }
  ],
  "images": [
    {
      "sha1": "string",
      "image_type": "main|lifestyle|detail|infographic|size_chart|color_options|spec_table|care_guide|certification|packaging|related_product|noise|other",
      "is_main_product": true,
      "used_for_attributes": true,
      "rejection_reason": ""
    }
  ],
  "ignored_observations": [
    {
      "sha1": "string",
      "observed": "string",
      "reason": "string"
    }
  ],
  "field_evidence": {
    "category": [],
    "brand": [],
    "target_gender": [],
    "category_attributes": {},
    "descriptions": {}
  },
  "quality_flags": [
    {
      "flag": "possible_image_contamination|conflicting_evidence|weak_material_evidence|marketing_claim_removed|needs_price_check|needs_manual_review",
      "detail": "string"
    }
  ],
  "trap_detection": {
    "is_trap": false,
    "trap_type": "",
    "explanation": "",
    "is_set_or_combo": false
  }
}

[최종 검수]
출력 전에 스스로 확인하라.
1. rejected_facts에 있던 정보가 tags/descriptions/category_attributes에 들어갔는가? 들어갔다면 제거.
2. related_product/model_styling_item/store_policy 정보를 본상품 속성에 썼는가? 썼다면 제거.
3. 근거 없는 연예인/인기/명품/프리미엄/알러지/정품 표현이 있는가? 있으면 제거.
4. SET 구성품과 같이 보이는 코디 상품을 섞었는가? 섞었다면 분리.
5. evidence가 없는 핵심 속성이 있는가? 있으면 null 또는 needs_review.
```

## 6. 요청 payload 형태

우리 LLM에 넘길 때는 아래처럼 `claude_output`을 통째로 넣되, 최종 LLM이 사용할 수 있는 증거 범위를 명시한다.

```json
{
  "product_meta": {
    "rv_product_id": 123,
    "advertiser_id": 7,
    "product_code": "EA1234",
    "product_name": "14K 핑크골드 귀걸이 EA1234",
    "price": 69000,
    "url": "https://example.com/product/EA1234"
  },
  "page_text": {
    "title": "14K 핑크골드 귀걸이 EA1234",
    "body_text": "상세 본문에서 추출한 텍스트...",
    "text_source": "extracted.json"
  },
  "claude_output": {
    "product_identity": {
      "main_product_name": "14K 핑크골드 귀걸이",
      "set_or_single": "single",
      "confidence": 0.91
    },
    "images": [
      {
        "sha1": "aaa111",
        "decision": "use",
        "product_scope": "main_product",
        "image_role": "main",
        "usable_facts": [
          {
            "fact": "핑크골드 톤의 귀걸이",
            "field_hint": "color",
            "confidence": 0.9
          }
        ],
        "rejected_facts": []
      },
      {
        "sha1": "bbb222",
        "decision": "ignore",
        "product_scope": "related_product",
        "image_role": "related_product",
        "usable_facts": [],
        "rejected_facts": [
          {
            "fact": "추천 목걸이 NA9999 39,000원",
            "reason": "추천 상품"
          }
        ]
      }
    ],
    "global_warnings": [
      "하단 추천상품 카드가 포함됨"
    ]
  }
}
```

## 7. 자동 테스트 설계

테스트는 검색 결과만 보지 말고, enrich 산출물과 검색 파서 결과를 같이 본다.

### Enrich 산출물 테스트

- 모든 입력 이미지 sha1이 `images[]`에 한 번 이상 등장해야 한다.
- `used_for_attributes=false`인 이미지의 OCR/관찰 단어가 `tags`, `descriptions`, `category_attributes`에 들어가면 실패.
- `related_products.target_name`에 있는 상품명이 본상품 `product.name` 또는 `set_components`로 들어가면 실패.
- `target_gender`는 구매자/착용 대상 근거가 있을 때만 값이 있어야 한다.
- `is_best_seller`, `is_premium`, `brand_tier=럭셔리/프리미엄`은 원문 근거가 없으면 실패.
- `descriptions` 4개는 모두 30~100자 범위, 광고 과장어 금지어가 있으면 실패.
- `tags[].tag_category`는 `situation|mood|feature`만 허용한다.
- `category_attributes`의 소재/스톤/색상은 서로 충돌하면 `quality_flags`가 있어야 한다.
- `acceptance.needs_review=true`인 상품은 임베딩 배치에서 제외한다.

### 검색 파서 테스트

- 특정 연예인 이름이 들어간 쿼리는 `keyword_filters`에 이름이 들어가야 한다.
- "남자연예인", "여자연예인"은 `target_gender`로 가지 않아야 한다.
- "여자친구 선물", "남자친구 선물"은 선물 대상 문맥이므로 구매자 성별로 강제하지 않는다.
- 명시 소재 `14K`, `925`, `니켈프리`, `알러지프리`는 검색 결과에서 근거가 없으면 상위 노출 실패.
- "추천상품 말고", "다른 상품 제외"는 ignored/related 상품이 결과에 섞이면 실패.
- must-match 쿼리에서 `keyword_filter_relaxed=true` 또는 `pe_filter_relaxed=true`이면 실패 후보로 기록한다.

## 8. 테스트 케이스 묶음

### 이미지 오염 fixture

1. 본상품 귀걸이 + 모델 착용 목걸이
   - 기대: 목걸이는 `model_styling_item`, 귀걸이만 본상품.

2. SET 상품 귀걸이+목걸이 + 추천 반지 카드
   - 기대: 귀걸이/목걸이는 `set_components`, 반지는 `related_products`.

3. 상세 인포그래픽 5장 중 2장이 다른 상품
   - 기대: 다른 상품 OCR은 `ignored_observations`, 설명문 미사용.

4. 배송/교환/반품 안내 이미지
   - 기대: `image_type=store_policy|noise`, `used_for_attributes=false`.

5. 브랜드 캠페인 배너에 "BEST, 연예인 PICK" 표시
   - 기대: 본상품 근거 아니면 `is_best_seller=null`, 연예인 태그 없음.

6. 호환 본체/리필/케이스가 함께 보이는 화장품 도구
   - 기대: compatible 본체 정보는 `related_products`, 도구 속성과 분리.

7. 공통 사이즈표와 상품 전용 사이즈표 혼재
   - 기대: 상품 전용만 size attributes, 공통표는 `use_limited` 또는 review.

8. 색상 옵션 이미지에 품절/다른 상품 코드 포함
   - 기대: 본 상품 옵션만 `options`, 다른 코드는 ignored.

### 속성 fixture

1. `14K 골드` vs `14K 도금` vs `골드필드`
   - 기대: 서로 섞지 않음.

2. `큐빅`을 다이아몬드처럼 표현하는 광고 문구
   - 기대: stone=큐빅, diamond 태그 없음.

3. `인공진주`와 `진주` 혼재
   - 기대: 원문 그대로, 불확실하면 review.

4. `니켈프리/알러지프리`가 배송 안내 이미지 하단 공통 문구
   - 기대: 본상품 근거 아니면 feature 태그 금지.

5. `백화점 정품`, `명품`, `프리미엄` 원문 근거 없음
   - 기대: 설명문/태그 생성 금지.

### 검색 쿼리 fixture

아래 쿼리는 `docs/search_quality_test_queries.md`의 100개와 함께 돌린다.

```text
1. 추천상품 말고 본상품만 보여줘
2. 모델이 같이 착용한 목걸이는 빼고 귀걸이만
3. 세트 사진에 보이는 반지도 포함된 상품
4. 귀걸이 단품인데 목걸이랑 같이 코디된 사진은 제외
5. 남자연예인이 착용한 여성용 말고 그냥 착용 귀걸이
6. 여자친구 선물용이지만 남성용은 제외
7. 14K 도금 말고 14K 골드 귀걸이
8. 큐빅 말고 다이아몬드 귀걸이
9. 인공진주 말고 천연 진주 반지
10. 알러지프리라고 확인된 귀걸이
11. 백화점 정품이라고 명시된 주얼리
12. 무료배송 문구 말고 실제 무료배송 상품
13. 당일출고 가능한 상품만
14. 하단 추천상품에 나온 목걸이 말고 현재 상품
15. 상세 이미지에 여러 상품 있는 것 중 본상품만
```

## 9. 운영 판정 기준

- `acceptance.confidence < 0.75` 또는 `needs_review=true`이면 임베딩하지 않는다.
- `possible_image_contamination`, `conflicting_evidence`, `needs_manual_review` flag가 있으면 검수 큐로 보낸다.
- 필터 LLM 실패 시 전체 이미지를 그대로 최종 속성에 쓰지 않는다. 전체 이미지를 보내더라도 image audit을 먼저 수행하고, `decision=use`인 근거만 사용한다.
- 테스트 배치에서는 상품별로 아래 값을 로그에 남긴다.
  - image_count, used_image_count, ignored_image_count
  - ignored_observations count
  - related_products count
  - quality_flags
  - desc_* 금지어 검출 결과
  - 검색 시 `keyword_filter_relaxed`, `pe_filter_relaxed`

## 10. 코드 반영 메모

- 현재 `call_claude_filter`는 실패하면 전체 이미지를 fallback으로 사용한다. 운영 품질 관점에서는 이 fallback이 오염 원인이 될 수 있다.
- 다음 개선은 단순 keep-list 대신 `image_audit` JSON을 반환하고, 최종 enrich prompt에 audit 결과를 넣는 방식이 적합하다.
- DB 컬럼은 당장 늘리지 않아도 `raw_response`에 `field_evidence`, `quality_flags`, `image_audit`를 넣을 수 있다. 운영 검수가 필요해지면 별도 컬럼으로 승격한다.
- `validate_claude.py`는 현재 텍스트 중심 검증이므로, 추후에는 `ignored_observations`와 `desc_*`/`tags` 교차 검사를 deterministic validator로 추가하는 것이 좋다.
