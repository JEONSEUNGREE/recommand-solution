-- PDF 멀티모달 실험 보고서 v1 기반 — Stage 2 산출물 적재 테이블
-- 케이스 04(에스티 로더) 패턴: 공통속성 + 카테고리별 JSONB + 태그 + 다관점 설명문

CREATE TABLE IF NOT EXISTS product_enriched (
    rv_product_id     BIGINT PRIMARY KEY REFERENCES rv_products(id) ON DELETE CASCADE,
    advertiser_id     BIGINT NOT NULL,
    product_code      TEXT   NOT NULL,

    -- 카테고리 자동 감지 + 신뢰도
    category          TEXT,
    category_confidence REAL,

    -- 공통 속성
    brand             TEXT,
    brand_tier        TEXT,     -- 럭셔리/프리미엄/중가/저가
    price_tier        TEXT,
    target_gender     TEXT,     -- 남성/여성/공용
    target_age_min    INT,
    target_age_max    INT,
    origin_country    TEXT,
    manufacturer      TEXT,

    -- 자유 속성 (카테고리별 동적)
    category_attributes JSONB,           -- {fit, size_chart, fabric, design_features, clinical_results, ...}
    compatible_products JSONB,           -- 트랩 회피: 호환 본체/관련 상품 분리 (케이스 03 헤라)
    set_components      JSONB,           -- SET 상품 구성 (지토 SET = 귀걸이 + 목걸이)

    -- 태그 (situation/mood/feature)
    tags              JSONB,             -- [{tag, category, confidence}]

    -- 다관점 설명문 (4종)
    desc_situation    TEXT,
    desc_material     TEXT,
    desc_style        TEXT,
    desc_persona      TEXT,

    -- 이미지 분류 결과
    image_types       JSONB,             -- [{sha1, type, note}]

    -- 메타
    enrich_method     TEXT,              -- 'claude_cli' / 'groq_vlm' / 'openai' 등
    model_used        TEXT,
    input_tokens      INT,
    output_tokens     INT,
    raw_response      TEXT,              -- 디버깅용 (필요 시 NULL 가능)
    enrich_error      TEXT,
    enriched_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_enriched_advertiser ON product_enriched(advertiser_id);
CREATE INDEX IF NOT EXISTS idx_product_enriched_category   ON product_enriched(category);
CREATE INDEX IF NOT EXISTS idx_product_enriched_brand      ON product_enriched(brand);
CREATE INDEX IF NOT EXISTS idx_product_enriched_attrs_gin  ON product_enriched USING gin (category_attributes);
CREATE INDEX IF NOT EXISTS idx_product_enriched_tags_gin   ON product_enriched USING gin (tags);
