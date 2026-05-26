-- 광고주별 이미지 URL 차단 패턴.
-- 다운로드 단계에서 src에 pattern이 포함되면 skip → 디스크/네트워크/VLM 비용 절약 + 임베딩 오염 차단.
CREATE TABLE IF NOT EXISTS advertiser_image_blocks (
    id              BIGSERIAL PRIMARY KEY,
    advertiser_id   BIGINT NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    pattern         TEXT   NOT NULL,         -- 부분 문자열 (substring 매치)
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_adv_blocks_lookup
    ON advertiser_image_blocks(advertiser_id) WHERE enabled = TRUE;
