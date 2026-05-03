CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS products (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'demo',
    sku             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    brand           TEXT,
    category        TEXT NOT NULL,
    subcategory     TEXT,
    gender          TEXT,
    season          TEXT,
    style           TEXT,
    color           TEXT,
    sizes           TEXT[],
    material        TEXT,
    price           INTEGER NOT NULL,
    sale_price      INTEGER,
    stock           INTEGER NOT NULL DEFAULT 0,
    description     TEXT,
    tags            TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_products_tenant     ON products(tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_products_category   ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_gender     ON products(gender);
CREATE INDEX IF NOT EXISTS idx_products_season     ON products(season);
CREATE INDEX IF NOT EXISTS idx_products_price      ON products(price);
CREATE INDEX IF NOT EXISTS idx_products_stock      ON products(stock) WHERE stock > 0;

CREATE TABLE IF NOT EXISTS product_embeddings (
    product_id      BIGINT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    embedding       vector(384),
    embedded_text   TEXT NOT NULL,
    model           TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
    ON product_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS outbox_events (
    id              BIGSERIAL PRIMARY KEY,
    aggregate_id    BIGINT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL,
    processed_at    TIMESTAMPTZ,
    retry_count     INTEGER DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed
    ON outbox_events(created_at) WHERE processed_at IS NULL;
