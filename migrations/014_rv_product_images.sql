-- 상품별 스크랩된 이미지 src URL 인덱스.
-- 광고주 내 여러 상품에 공통으로 등장하는 이미지(배너·광고 등) 탐지에 사용.
CREATE TABLE IF NOT EXISTS rv_product_images (
    id             BIGSERIAL PRIMARY KEY,
    advertiser_id  BIGINT       NOT NULL,
    product_code   TEXT         NOT NULL,
    rv_product_id  BIGINT,
    src_url        TEXT         NOT NULL,
    local_filename TEXT,
    created_at     TIMESTAMPTZ  DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_rv_product_images_code_src
    ON rv_product_images (advertiser_id, product_code, src_url);

CREATE INDEX IF NOT EXISTS idx_rv_product_images_adv_src
    ON rv_product_images (advertiser_id, src_url);
