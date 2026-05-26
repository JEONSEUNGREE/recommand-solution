-- 광고주별 selector override: 자동 탐지 대신 수동 지정한 셀렉터를 우선 사용.
-- 비워두면 플랫폼 디폴트 (#productDetail / #prdDetail 등)로 폴백.
ALTER TABLE advertisers
    ADD COLUMN IF NOT EXISTS selector_name    TEXT,   -- 상품명/옵션 영역 (예: '.info')
    ADD COLUMN IF NOT EXISTS selector_detail  TEXT,   -- 상품 상세 본문 영역 (예: '#dd01')
    ADD COLUMN IF NOT EXISTS selector_price   TEXT,   -- 가격 영역 (선택)
    ADD COLUMN IF NOT EXISTS image_attrs      TEXT;   -- lazy 이미지 속성 우선순위 (콤마구분, 예: 'data-frz-src,data-src,src')
