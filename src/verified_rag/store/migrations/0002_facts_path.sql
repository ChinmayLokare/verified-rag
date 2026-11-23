-- M1 facts path: alias priority, calculation period type, read-only views, and the reader role.

ALTER TABLE concept_aliases ADD COLUMN priority integer NOT NULL DEFAULT 0;  -- lower wins
ALTER TABLE calculations ADD COLUMN period_type text
    CHECK (period_type IN ('instant', 'duration'));
ALTER TABLE facts ADD COLUMN source_tag text;  -- e.g. us-gaap:Revenues

CREATE INDEX facts_entity_concept_period_idx
    ON facts (entity_id, concept_id, period_end) WHERE is_latest_reported;

-- Views are the only things the agent's data access can read. Facts from documents that
-- did not finish ingesting are hidden.

CREATE VIEW v_entities AS
SELECT entity_id, pack_id, external_id, name, aliases, attributes
FROM entities;

CREATE VIEW v_concepts AS
SELECT concept_id, pack_id, name, description, unit_kind
FROM concepts;

CREATE VIEW v_concept_aliases AS
SELECT a.concept_id, c.pack_id, c.name AS concept_name, a.alias, a.source, a.priority
FROM concept_aliases a
JOIN concepts c ON c.concept_id = a.concept_id;

CREATE VIEW v_facts AS
SELECT f.fact_id,
       f.pack_id,
       f.entity_id,
       e.name           AS entity_name,
       f.concept_id,
       c.name           AS concept_name,
       f.value,
       f.unit,
       f.scale,
       f.period_start,
       f.period_end,
       f.period_type,
       f.fiscal_year,
       f.fiscal_period,
       f.dimensions,
       f.is_latest_reported,
       f.document_id,
       d.external_id    AS document_external_id,
       d.doc_type,
       d.filed_at,
       d.source_uri,
       f.source_tag,
       f.table_id,
       f.source_anchor
FROM facts f
JOIN entities e  ON e.entity_id = f.entity_id
JOIN concepts c  ON c.concept_id = f.concept_id
JOIN documents d ON d.document_id = f.document_id
WHERE d.status = 'indexed';

CREATE VIEW v_calculations AS
SELECT calc_id, request_id, expression, input_refs, result, unit, period_type, created_at
FROM calculations;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vrag_reader') THEN
        CREATE ROLE vrag_reader NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO vrag_reader;
GRANT SELECT ON v_entities, v_concepts, v_concept_aliases, v_facts, v_calculations
    TO vrag_reader;
