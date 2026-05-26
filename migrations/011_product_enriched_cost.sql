-- 상품당 enrich 비용 추적.
-- Claude envelope 의 total_cost_usd 를 그대로 적재 (cache hit/creation 모두 반영된 실제 청구가).
-- cache_read/cache_creation은 raw_response.usage에 들어있지만 캐시 효율 보려면 별도 컬럼이 편함.

ALTER TABLE product_enriched
    ADD COLUMN IF NOT EXISTS cost_usd                  NUMERIC(12, 6),
    ADD COLUMN IF NOT EXISTS cache_read_input_tokens   INTEGER,
    ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS duration_ms               INTEGER;

CREATE INDEX IF NOT EXISTS idx_product_enriched_cost ON product_enriched(cost_usd);
