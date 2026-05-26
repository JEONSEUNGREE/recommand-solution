-- 광고주별 selector를 1:N로 관리. 같은 role(name/detail/price)에 여러 selector를 priority 순으로 시도.
CREATE TABLE IF NOT EXISTS advertiser_selectors (
    id              BIGSERIAL PRIMARY KEY,
    advertiser_id   BIGINT NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    role            TEXT   NOT NULL CHECK (role IN ('name', 'detail', 'price')),
    selector        TEXT   NOT NULL,
    priority        SMALLINT NOT NULL DEFAULT 100,   -- 낮을수록 먼저 시도
    enabled         BOOLEAN  NOT NULL DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_adv_selectors_lookup
    ON advertiser_selectors(advertiser_id, role, priority) WHERE enabled = TRUE;

-- 기존 단일 컬럼 데이터 옮기기 (NULL/빈 문자열은 skip)
INSERT INTO advertiser_selectors (advertiser_id, role, selector, priority)
SELECT id, 'name', selector_name, 100
FROM advertisers
WHERE selector_name IS NOT NULL AND selector_name <> '';

INSERT INTO advertiser_selectors (advertiser_id, role, selector, priority)
SELECT id, 'detail', selector_detail, 100
FROM advertisers
WHERE selector_detail IS NOT NULL AND selector_detail <> '';

INSERT INTO advertiser_selectors (advertiser_id, role, selector, priority)
SELECT id, 'price', selector_price, 100
FROM advertisers
WHERE selector_price IS NOT NULL AND selector_price <> '';

-- 옛 단일 컬럼은 일단 유지 (코드 안정성). 추후 drop.
