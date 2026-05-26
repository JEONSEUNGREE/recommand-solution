-- search_logs 기본 테이블 + LLM 토큰/비용 컬럼.
-- 루트의 setup_search_logs.py 가 만들던 정의를 흡수 (VIT-4).
-- 본 파일이 search_logs 의 정식 출처. 015/016 은 추가 컬럼/관련 테이블만 다룸.
CREATE TABLE IF NOT EXISTS search_logs (
    id                     BIGSERIAL PRIMARY KEY,
    query                  TEXT        NOT NULL,
    semantic_query         TEXT,
    llm_used               BOOLEAN     NOT NULL DEFAULT FALSE,
    llm_provider           TEXT,
    backend                TEXT,
    advertiser_id          INTEGER,
    result_count           INTEGER     NOT NULL,
    keyword_filters        TEXT[],
    keyword_filter_relaxed BOOLEAN              DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_logs_created      ON search_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_logs_result_count ON search_logs(result_count);

ALTER TABLE search_logs
    ADD COLUMN IF NOT EXISTS llm_input_tokens  INTEGER,
    ADD COLUMN IF NOT EXISTS llm_output_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS llm_cost_usd      NUMERIC(12, 8);
