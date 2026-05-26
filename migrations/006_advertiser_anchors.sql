-- 광고주의 상품 상세 본문을 HTML 주석/문자열 anchor 사이로 슬라이스.
-- CSS selector보다 우선순위 높음 (anchor 있으면 거기서 slice).
ALTER TABLE advertisers
    ADD COLUMN IF NOT EXISTS detail_anchor_start TEXT,
    ADD COLUMN IF NOT EXISTS detail_anchor_end   TEXT;
