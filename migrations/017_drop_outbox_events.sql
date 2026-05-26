-- outbox_events 제거 (VIT-4).
-- 001_init.sql 에서 정의됐지만 코드베이스 전체 grep 결과 producer/consumer 없음.
-- ProductEnrichController 등이 데몬 스레드로 직접 enrich 처리 — outbox 패턴 미적용.
-- 추후 outbox 패턴이 필요해지면 새 마이그레이션으로 다시 정의.
DROP INDEX IF EXISTS idx_outbox_unprocessed;
DROP TABLE IF EXISTS outbox_events;
