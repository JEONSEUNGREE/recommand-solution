-- 광고주 (호스팅 + 인증 키 보관)
CREATE TABLE IF NOT EXISTS advertisers (
    id                BIGSERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    host_type         TEXT NOT NULL CHECK (host_type IN ('makeshop', 'cafe24', 'godo', 'imweb', 'custom')),
    shop_url          TEXT NOT NULL,           -- 예: https://www.pippin.co.kr
    shop_key          TEXT,                    -- makeshop Shopkey
    license_key       TEXT,                    -- makeshop Licensekey
    cafe24_mall_id    TEXT,
    cafe24_access_token TEXT,
    cafe24_refresh_token TEXT,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_advertisers_host ON advertisers(host_type);

-- RV_PRODUCTS: ireview-admin과 동일한 컨셉, 외부 쇼핑몰에서 동기화한 원본 상품 보관
CREATE TABLE IF NOT EXISTS rv_products (
    id                  BIGSERIAL PRIMARY KEY,
    advertiser_id       BIGINT NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    product_code        TEXT NOT NULL,         -- makeshop uid / cafe24 product_no
    product_origin_code TEXT,                  -- makeshop brandcode / cafe24 product_code
    product_name        TEXT NOT NULL,
    company_nm          TEXT,                  -- cate1 또는 카테고리명
    price               NUMERIC(15, 2),
    sale_price          NUMERIC(15, 2),
    stock               INTEGER,
    product_url         TEXT,
    image_url           TEXT,
    raw_payload         JSONB,                 -- 원본 응답 통째로 보관 (나중에 enrichment용)
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reg_date            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mod_date            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rv_products_advertiser_code UNIQUE (advertiser_id, product_code)
);

CREATE INDEX IF NOT EXISTS idx_rv_products_advertiser ON rv_products(advertiser_id);
CREATE INDEX IF NOT EXISTS idx_rv_products_synced     ON rv_products(synced_at DESC);

-- 동기화 상태 추적 (재개 / 진행률 표시용)
CREATE TABLE IF NOT EXISTS product_sync_status (
    advertiser_id   BIGINT NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    sync_type       TEXT NOT NULL DEFAULT 'PRODUCT_LIST',
    status          TEXT NOT NULL,             -- IN_PROGRESS / COMPLETED / FAILED
    last_page       INTEGER NOT NULL DEFAULT 0,
    total_count     INTEGER,
    saved_count     INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (advertiser_id, sync_type)
);
