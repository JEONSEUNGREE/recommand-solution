-- 한국어 형태소 기반 sparse 인덱스 (Kiwi + TF-IDF).
-- BGE-M3 토크나이저의 한국어 음절 분해 한계 보완 — 단어 단위 매칭이 가능해진다.
--
-- 구조:
--   morpheme_vocab           — 단어(형태소) → 인덱스 사전 + 사전구축시점 IDF
--   product_descriptions.embedding_morpheme sparsevec(65536)
--
-- 동작:
--   백필 시 Kiwi 로 description 분석 → 의미 형태소 추출 → TF-IDF 가중치 →
--   {vocab_idx: weight} 를 sparsevec 으로 저장.
--   검색 시 쿼리도 같은 vocab 으로 인코딩 → 내적(<#>)으로 매칭.

CREATE TABLE IF NOT EXISTS morpheme_vocab (
    idx       integer PRIMARY KEY,             -- sparsevec 1-based index
    morpheme  text    NOT NULL UNIQUE,         -- 형태소 표층형 (e.g., "여름", "데이트")
    df        integer NOT NULL,                 -- document frequency (이 단어가 나온 문서 수)
    idf       real    NOT NULL,                 -- smoothed: log((N+1)/(df+1))+1
    built_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_morpheme_vocab_morpheme ON morpheme_vocab(morpheme);

ALTER TABLE product_descriptions
    ADD COLUMN IF NOT EXISTS embedding_morpheme sparsevec(65536);

-- 행 수가 수천 단위라 brute-force 스캔으로 충분. 별도 인덱스는 두지 않음.
-- (pgvector 0.8 sparsevec HNSW 는 이 규모에선 불필요.)
