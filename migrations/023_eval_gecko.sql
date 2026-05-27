-- Gecko(arXiv:2403.20327) / 오늘의집 스타일 검색 평가.
-- LLM(gpt-4o-mini)이 상품 설명문에서 검색어를 생성 → 그 상품을 gold(정답) 로 두고
-- 검색기가 gold 를 얼마나 상위로 올리는지 NDCG@10 / Recall@10 / MRR 로 측정.
--
-- eval_sets    : 평가셋 1회 생성 단위 (상품 N개 × 쿼리 M개)
-- eval_queries : LLM 이 만든 쿼리 + gold 상품(보장된 positive)
-- eval_runs    : 한 평가셋에 대해 특정 검색 설정으로 측정한 결과(집계 지표)
-- eval_run_items : run 내 쿼리별 상세(gold 순위, per-query NDCG 등) — 드릴다운용

CREATE TABLE IF NOT EXISTS eval_sets (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT,
    advertiser_id BIGINT,
    n_products    INT NOT NULL,
    n_per_product INT NOT NULL,
    n_queries     INT NOT NULL DEFAULT 0,
    llm_model     TEXT,
    llm_cost_usd  NUMERIC(14,8) NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_queries (
    id            BIGSERIAL PRIMARY KEY,
    eval_set_id   BIGINT NOT NULL REFERENCES eval_sets(id) ON DELETE CASCADE,
    rv_product_id BIGINT NOT NULL,         -- gold/source 상품 (보장된 positive)
    advertiser_id BIGINT,
    query         TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_queries_set ON eval_queries(eval_set_id);

CREATE TABLE IF NOT EXISTS eval_runs (
    id              BIGSERIAL PRIMARY KEY,
    eval_set_id     BIGINT NOT NULL REFERENCES eval_sets(id) ON DELETE CASCADE,
    backend         TEXT NOT NULL DEFAULT 'bge',
    mode            TEXT NOT NULL DEFAULT 'dense',
    fusion          TEXT,
    alpha           REAL,
    use_morpheme    BOOLEAN NOT NULL DEFAULT false,
    use_colbert     BOOLEAN NOT NULL DEFAULT false,
    llm_query_parse BOOLEAN NOT NULL DEFAULT false,  -- 검색 시 LLM 쿼리 정제 사용 여부
    k               INT NOT NULL DEFAULT 10,
    n_queries       INT NOT NULL DEFAULT 0,
    ndcg_at_10      NUMERIC(6,4),
    recall_at_10    NUMERIC(6,4),
    mrr             NUMERIC(6,4),
    duration_ms     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_set ON eval_runs(eval_set_id, created_at DESC);

CREATE TABLE IF NOT EXISTS eval_run_items (
    id                 BIGSERIAL PRIMARY KEY,
    eval_run_id        BIGINT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    eval_query_id      BIGINT NOT NULL,
    query              TEXT,
    gold_rv_product_id BIGINT,
    gold_rank          INT,                 -- 1-based, top-k 밖이면 NULL
    ndcg_at_10         NUMERIC(6,4) NOT NULL DEFAULT 0,
    reciprocal_rank    NUMERIC(6,4) NOT NULL DEFAULT 0,
    hit                BOOLEAN NOT NULL DEFAULT false,
    top_ids            BIGINT[],            -- 검색된 top-k rv_product_id (드릴다운)
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_run_items_run ON eval_run_items(eval_run_id);
