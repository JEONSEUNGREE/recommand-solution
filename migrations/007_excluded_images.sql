-- 상품별 OCR/VLM 처리 제외 이미지. 마킹되면 다음 enrich에서 skip.
CREATE TABLE IF NOT EXISTS rv_product_excluded_images (
    id              BIGSERIAL PRIMARY KEY,
    advertiser_id   BIGINT NOT NULL,
    product_code    TEXT   NOT NULL,
    image_filename  TEXT   NOT NULL,    -- sha1.ext (디스크 파일명)
    image_url       TEXT,                -- 원본 URL (참고용)
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (advertiser_id, product_code, image_filename)
);

CREATE INDEX IF NOT EXISTS idx_excl_images_product
    ON rv_product_excluded_images(advertiser_id, product_code);
