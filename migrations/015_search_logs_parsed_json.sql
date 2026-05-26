-- LLM 파싱 결과 전체(filters + notes + provider) 저장 — 파이프라인 디버깅용
ALTER TABLE search_logs
    ADD COLUMN IF NOT EXISTS llm_parsed_json JSONB;
