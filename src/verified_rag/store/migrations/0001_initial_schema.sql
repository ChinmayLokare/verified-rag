-- Core schema from docs/design.md section 6. Domain-neutral: packs supply the vocabulary.
-- Embedding width (384) matches the small open embedding models planned in the design.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE entities (
    entity_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pack_id      text   NOT NULL,
    external_id  text   NOT NULL,                     -- finance: CIK
    name         text   NOT NULL,
    aliases      text[] NOT NULL DEFAULT '{}',
    attributes   jsonb  NOT NULL DEFAULT '{}',        -- finance: ticker, fiscal_year_end
    UNIQUE (pack_id, external_id)
);

CREATE TABLE documents (
    document_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pack_id         text        NOT NULL,
    entity_id       bigint      NOT NULL REFERENCES entities,
    external_id     text        NOT NULL,             -- finance: accession number
    doc_type        text        NOT NULL,             -- finance: 10-K | 10-Q
    period_end      date,
    filed_at        timestamptz,
    source_uri      text        NOT NULL,
    content_hash    text        NOT NULL,
    parser_version  text        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'indexed', 'failed')),
    error           text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pack_id, external_id)
);
CREATE INDEX documents_entity_idx ON documents (entity_id, period_end);

CREATE TABLE concepts (
    concept_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pack_id      text NOT NULL,
    name         text NOT NULL,                       -- canonical, e.g. revenue
    description  text,
    unit_kind    text NOT NULL,                       -- e.g. monetary, shares, ratio
    UNIQUE (pack_id, name)
);

CREATE TABLE concept_aliases (
    concept_id  bigint NOT NULL REFERENCES concepts ON DELETE CASCADE,
    alias       text   NOT NULL,                      -- label text or tag name
    source      text   NOT NULL,                      -- e.g. curated, xbrl_tag, suggested
    PRIMARY KEY (concept_id, alias)
);
CREATE INDEX concept_aliases_alias_idx ON concept_aliases (lower(alias));

CREATE TABLE facts (
    fact_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pack_id             text    NOT NULL,
    entity_id           bigint  NOT NULL REFERENCES entities,
    document_id         bigint  NOT NULL REFERENCES documents ON DELETE CASCADE,
    concept_id          bigint  NOT NULL REFERENCES concepts,
    value               numeric NOT NULL,
    unit                text    NOT NULL,
    scale               integer NOT NULL DEFAULT 0,   -- power of ten applied to value
    period_start        date,
    period_end          date    NOT NULL,
    period_type         text    NOT NULL CHECK (period_type IN ('instant', 'duration')),
    fiscal_year         integer,
    fiscal_period       text,                         -- e.g. FY, Q1
    dimensions          jsonb,                        -- NULL = whole entity
    is_latest_reported  boolean NOT NULL DEFAULT false,
    table_id            text,
    row_label           text,
    col_label           text,
    source_anchor       text,
    CHECK (
        (period_type = 'instant'  AND period_start IS NULL) OR
        (period_type = 'duration' AND period_start IS NOT NULL AND period_start <= period_end)
    )
);
CREATE INDEX facts_lookup_idx ON facts (entity_id, concept_id, fiscal_year, fiscal_period);
CREATE INDEX facts_document_idx ON facts (document_id);
-- At most one latest-reported value per (entity, concept, period, dimensions).
CREATE UNIQUE INDEX facts_one_latest_idx
    ON facts (entity_id, concept_id, period_start, period_end, dimensions)
    NULLS NOT DISTINCT
    WHERE is_latest_reported;

CREATE TABLE chunks (
    chunk_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pack_id            text    NOT NULL,
    document_id        bigint  NOT NULL REFERENCES documents ON DELETE CASCADE,
    section            text,
    table_id           text,                          -- set when the chunk is a table
    text               text    NOT NULL,
    context_header     text    NOT NULL,
    embedding          vector(384),
    embedding_model    text,                          -- model name and version used
    injection_flagged  boolean NOT NULL DEFAULT false,
    tsv                tsvector GENERATED ALWAYS AS (
                           to_tsvector('english'::regconfig, context_header || ' ' || text)
                       ) STORED,
    CHECK ((embedding IS NULL) = (embedding_model IS NULL))
);
CREATE INDEX chunks_document_idx ON chunks (document_id, section);
CREATE INDEX chunks_table_idx ON chunks (table_id) WHERE table_id IS NOT NULL;
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE requests (
    request_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     text        NOT NULL,
    question    text        NOT NULL,
    status      text        NOT NULL,
    confidence  numeric,
    cost_usd    numeric(12, 6),
    latency_ms  integer,
    trace_id    text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX requests_user_idx ON requests (user_id, created_at);

CREATE TABLE calculations (
    calc_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id  uuid    REFERENCES requests ON DELETE CASCADE,
    expression  text    NOT NULL,
    input_refs  jsonb   NOT NULL,                     -- {"a": "f_102", "b": "f_101"}
    result      numeric NOT NULL,
    unit        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX calculations_request_idx ON calculations (request_id);
