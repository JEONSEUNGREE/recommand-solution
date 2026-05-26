-- rv_products에 enrich 단계 컬럼 추가
-- 워크플로: scrape (html+이미지) → enrich (ocr 또는 vlm, 둘 중 하나)
ALTER TABLE rv_products
    ADD COLUMN IF NOT EXISTS body_text          TEXT,
    ADD COLUMN IF NOT EXISTS body_text_len      INTEGER,
    ADD COLUMN IF NOT EXISTS html_path          TEXT,                   -- D:\recommand-data\... 상대경로
    ADD COLUMN IF NOT EXISTS image_dir          TEXT,                   -- 다운로드된 이미지 디렉토리
    ADD COLUMN IF NOT EXISTS image_local_count  INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS scrape_status      TEXT,                   -- 'scraped' | 'failed' | NULL=미처리
    ADD COLUMN IF NOT EXISTS scraped_at         TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS scrape_error       TEXT,
    ADD COLUMN IF NOT EXISTS enrich_method      TEXT,                   -- 'ocr' | 'vlm'
    ADD COLUMN IF NOT EXISTS enriched_info      TEXT,                   -- 임베딩에 들어갈 최종 정제 텍스트
    ADD COLUMN IF NOT EXISTS enriched_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS enrich_error       TEXT;

CREATE INDEX IF NOT EXISTS idx_rv_products_scrape_status ON rv_products(scrape_status);
CREATE INDEX IF NOT EXISTS idx_rv_products_enrich_method ON rv_products(enrich_method);
