-- PDF v1.0 LLM 추출 전략의 Step 2(검증)/Step 3(보정)/Step 4(임베딩) 단계별 컬럼.
-- 1번 통합 호출(enrich_claude.py)로 적재된 product_enriched 행에 누적 갱신.

-- ─────────────────────────────────────────────────────────────
-- A. product_enriched 단계별 상태 + 검증/보정 컬럼
-- ─────────────────────────────────────────────────────────────
ALTER TABLE product_enriched
    -- Step 2 검증 (validate_claude.py)
    ADD COLUMN IF NOT EXISTS validation_score    REAL,
    ADD COLUMN IF NOT EXISTS validation_warnings JSONB,
    ADD COLUMN IF NOT EXISTS validation_errors   JSONB,
    ADD COLUMN IF NOT EXISTS trap_detected       JSONB,
    ADD COLUMN IF NOT EXISTS validated_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS validated_by        TEXT,

    -- Step 3 보정 (refine_claude.py — 선택)
    ADD COLUMN IF NOT EXISTS refined_fields      JSONB,
    ADD COLUMN IF NOT EXISTS refined_at          TIMESTAMPTZ,

    -- 전체 진행 상태
    -- extracted = 1차 추출만
    -- validated = 검증까지 통과
    -- refined   = 보정까지 완료
    -- embedded  = 벡터 적재 완료
    -- failed    = 실패 (재시도 대상)
    ADD COLUMN IF NOT EXISTS enrich_status       TEXT DEFAULT 'extracted',
    ADD COLUMN IF NOT EXISTS enrich_version      INT  DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_product_enriched_status ON product_enriched(enrich_status);

-- ─────────────────────────────────────────────────────────────
-- B. product_descriptions — Step 4 임베딩 적재 테이블
--    한 상품당 perspective(situation/material/style/persona) 4행.
--    1536차원은 text-embedding-3-small 기준.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_descriptions (
    id              BIGSERIAL PRIMARY KEY,
    rv_product_id   BIGINT NOT NULL REFERENCES rv_products(id) ON DELETE CASCADE,
    advertiser_id   BIGINT NOT NULL,
    perspective     TEXT   NOT NULL CHECK (perspective IN ('situation','material','style','persona')),
    description     TEXT   NOT NULL,
    embedding       vector(1536),
    model           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (rv_product_id, perspective)
);

-- HNSW 인덱스 (코사인). 한 상품당 4벡터라 작은 검색셋도 빠르게.
CREATE INDEX IF NOT EXISTS idx_product_descriptions_embedding
    ON product_descriptions USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_product_descriptions_perspective
    ON product_descriptions(perspective);
CREATE INDEX IF NOT EXISTS idx_product_descriptions_advertiser
    ON product_descriptions(advertiser_id);
