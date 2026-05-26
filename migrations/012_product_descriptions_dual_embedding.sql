-- product_descriptions에 두 가지 임베딩 모델을 병행 보관.
-- bge-m3 (1024차원, 로컬 embedder)와 OpenAI text-embedding-3-small (1536차원).
-- 둘 다 채워두면 검색 시 advertiser 선택과 함께 임베딩 모델도 선택 가능.

-- 기존 embedding(vector 1536) 컬럼은 우선 그대로 두고, bge용을 추가.
-- 데이터는 아직 들어가지 않았으니 destructive 변경도 안전.
ALTER TABLE product_descriptions
    RENAME COLUMN embedding TO embedding_openai;

ALTER TABLE product_descriptions
    ADD COLUMN IF NOT EXISTS embedding_bge vector(1024);

-- HNSW 인덱스 — 모델별로 따로
DROP INDEX IF EXISTS idx_product_descriptions_embedding;
CREATE INDEX IF NOT EXISTS idx_product_desc_openai_hnsw
    ON product_descriptions USING hnsw (embedding_openai vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_product_desc_bge_hnsw
    ON product_descriptions USING hnsw (embedding_bge vector_cosine_ops);
