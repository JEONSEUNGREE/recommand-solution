-- search_logs: 클라이언트 버전 구분(v1.0=chat.html, v1.1=chat2.html) + v1.1 LLM 나레이션 저장.
-- 기존 행은 모두 전부 chat.html(v1.0)에서 쌓인 것이므로 DEFAULT '1.0' 으로 백필된다.
-- narration: chat2(v1.1)가 /narrate 로 받은 LLM 답변(intro + 상품별 이유 + _usage)을
--            그대로 보관 → 이력 클릭 시 동일하게 재현.
ALTER TABLE search_logs
    ADD COLUMN IF NOT EXISTS version   TEXT NOT NULL DEFAULT '1.0',
    ADD COLUMN IF NOT EXISTS narration JSONB;

CREATE INDEX IF NOT EXISTS idx_search_logs_version ON search_logs(version, created_at DESC);
