-- search_logs 타이밍 컬럼 + 검색 결과 스냅샷 테이블.
-- 루트의 migrate_search_logs_v2.py 가 만들던 정의를 흡수 (VIT-4).
-- 013 에서 search_logs 기본 테이블이 만들어진 뒤 본 파일이 컬럼/스냅샷 테이블을 보강한다.
ALTER TABLE search_logs
    ADD COLUMN IF NOT EXISTS llm_duration_ms   INTEGER,
    ADD COLUMN IF NOT EXISTS embed_duration_ms INTEGER,
    ADD COLUMN IF NOT EXISTS total_duration_ms INTEGER;

CREATE TABLE IF NOT EXISTS search_log_results (
    id             BIGSERIAL PRIMARY KEY,
    log_id         BIGINT NOT NULL REFERENCES search_logs(id) ON DELETE CASCADE,
    rank           SMALLINT NOT NULL,
    rv_product_id  INTEGER NOT NULL,
    product_name   TEXT,
    product_code   TEXT,
    image_url      TEXT,
    product_url    TEXT,
    price          INTEGER,
    sale_price     INTEGER,
    category       TEXT,
    brand          TEXT,
    distance       FLOAT NOT NULL,
    perspective    TEXT,
    description    TEXT,
    tags           JSONB,
    desc_persona   TEXT
);

CREATE INDEX IF NOT EXISTS idx_slr_log_id ON search_log_results(log_id);
