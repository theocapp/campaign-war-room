-- ============================================================
-- Political Knowledge Graph Schema (Postgres-compatible)
-- Prefix: kg_  (avoids conflicts with existing issues/narratives tables)
-- ============================================================
-- Creation order matters: tables referenced by FKs appear first.
--   kg_entities → kg_entity_aliases
--   kg_sources  → kg_events → kg_narratives → kg_narrative_claims
--   kg_sources  → kg_claims → kg_claim_entities, kg_claim_issues
--   kg_edges    (polymorphic — no FKs to node tables)
-- ============================================================


-- ── Enum types ───────────────────────────────────────────────────────────────

CREATE TYPE kg_entity_type      AS ENUM ('PERSON', 'ORG', 'ISSUE', 'PLACE');
CREATE TYPE kg_stance           AS ENUM ('support', 'oppose', 'neutral', 'unknown');
CREATE TYPE kg_event_type       AS ENUM ('DEBATE', 'SCANDAL', 'POLICY', 'SPEECH', 'VOTE');
CREATE TYPE kg_relationship_type AS ENUM (
    'SUPPORTS', 'REFUTES', 'MENTIONS', 'RELATES_TO', 'OCCURRED_IN'
);
-- Valid node type labels used in kg_edges.from_type / to_type.
CREATE TYPE kg_node_type AS ENUM ('entity', 'claim', 'issue', 'source', 'event', 'narrative');


-- ── Entities ─────────────────────────────────────────────────────────────────

CREATE TABLE kg_entities (
    id                    SERIAL PRIMARY KEY,
    entity_type           kg_entity_type  NOT NULL,

    -- Extracted name (as it appeared in source text)
    name                  TEXT            NOT NULL,
    -- Resolution-confirmed canonical form; NULL means name is already canonical
    canonical_name        TEXT,

    description           TEXT,
    extra_data            JSONB           DEFAULT '{}',

    -- Embedding vector (JSON float array placeholder; swap for VECTOR(1536) with pgvector)
    embedding             TEXT,
    -- 0.0–1.0 confidence that this record is correctly resolved
    resolution_confidence REAL            CHECK (
        resolution_confidence IS NULL OR
        (resolution_confidence >= 0.0 AND resolution_confidence <= 1.0)
    ),
    -- Points to the surviving entity when this one was merged; NULL = canonical
    merged_into_entity_id INTEGER         REFERENCES kg_entities(id) ON DELETE SET NULL,

    created_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE kg_entity_aliases (
    id        SERIAL  PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    alias     TEXT    NOT NULL,
    UNIQUE (entity_id, alias)
);

CREATE INDEX idx_kg_entities_name             ON kg_entities(name);
CREATE INDEX idx_kg_entities_canonical_name   ON kg_entities(canonical_name)
    WHERE canonical_name IS NOT NULL;
CREATE INDEX idx_kg_entities_type             ON kg_entities(entity_type);
CREATE INDEX idx_kg_entities_merged_into      ON kg_entities(merged_into_entity_id)
    WHERE merged_into_entity_id IS NOT NULL;
CREATE INDEX idx_kg_entity_aliases_alias      ON kg_entity_aliases(alias);


-- ── Sources ──────────────────────────────────────────────────────────────────

CREATE TABLE kg_sources (
    id             SERIAL      PRIMARY KEY,
    url            TEXT        NOT NULL,
    title          TEXT,
    text           TEXT,
    published_at   TIMESTAMPTZ,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- SHA-256 of (url || text) — primary dedup key
    content_hash   TEXT        NOT NULL UNIQUE,
    -- Soft back-reference to the existing ingestion pipeline row (no FK)
    source_item_id INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_kg_sources_url          ON kg_sources(url);
CREATE INDEX idx_kg_sources_content_hash ON kg_sources(content_hash);
CREATE INDEX idx_kg_sources_ingested_at  ON kg_sources(ingested_at);


-- ── Issues (normalized topic clusters) ───────────────────────────────────────

CREATE TABLE kg_issues (
    id           SERIAL      PRIMARY KEY,
    -- Normalized slug, e.g. "housing_affordability"
    name         TEXT        NOT NULL UNIQUE,
    -- Human-readable label, e.g. "Housing Affordability"
    display_name TEXT        NOT NULL,
    description  TEXT,
    -- Embedding vector placeholder (JSON float array)
    embedding    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_kg_issues_name ON kg_issues(name);


-- ── Events (first-class nodes) ───────────────────────────────────────────────

CREATE TABLE kg_events (
    id                SERIAL          PRIMARY KEY,
    name              TEXT            NOT NULL,
    event_type        kg_event_type   NOT NULL,
    -- When the event occurred (distinct from ingestion time)
    event_timestamp   TIMESTAMPTZ,
    description       TEXT,
    -- Primary source that documents this event (soft ref ok if source may not exist yet)
    related_source_id INTEGER         REFERENCES kg_sources(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_kg_events_type      ON kg_events(event_type);
CREATE INDEX idx_kg_events_timestamp ON kg_events(event_timestamp);


-- ── Claims ───────────────────────────────────────────────────────────────────

CREATE TABLE kg_claims (
    id           SERIAL      PRIMARY KEY,
    text         TEXT        NOT NULL,
    stance       kg_stance   NOT NULL DEFAULT 'unknown',
    confidence   REAL        NOT NULL DEFAULT 0.0
                     CHECK (confidence >= 0.0 AND confidence <= 1.0),
    source_id    INTEGER     NOT NULL REFERENCES kg_sources(id) ON DELETE CASCADE,
    -- Stable semantic fingerprint for near-duplicate detection; NULL until computed
    semantic_id  TEXT,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Dedup: prevent identical (source, text) pairs.
CREATE UNIQUE INDEX idx_kg_claims_source_text
    ON kg_claims(source_id, md5(text));

-- Semantic dedup: find claims sharing the same embedding bucket.
CREATE INDEX idx_kg_claims_semantic_id ON kg_claims(semantic_id)
    WHERE semantic_id IS NOT NULL;

CREATE INDEX idx_kg_claims_stance     ON kg_claims(stance);
CREATE INDEX idx_kg_claims_confidence ON kg_claims(confidence);


-- ── Claim ↔ Entity (M:M) ─────────────────────────────────────────────────────

CREATE TABLE kg_claim_entities (
    claim_id  INTEGER NOT NULL REFERENCES kg_claims(id)   ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    PRIMARY KEY (claim_id, entity_id)
);

CREATE INDEX idx_kg_claim_entities_entity ON kg_claim_entities(entity_id);


-- ── Claim ↔ Issue (M:M) ──────────────────────────────────────────────────────

CREATE TABLE kg_claim_issues (
    claim_id INTEGER NOT NULL REFERENCES kg_claims(id)  ON DELETE CASCADE,
    issue_id INTEGER NOT NULL REFERENCES kg_issues(id)  ON DELETE CASCADE,
    PRIMARY KEY (claim_id, issue_id)
);

CREATE INDEX idx_kg_claim_issues_issue ON kg_claim_issues(issue_id);


-- ── Narratives (evolving claim clusters) ─────────────────────────────────────

CREATE TABLE kg_narratives (
    id                SERIAL      PRIMARY KEY,
    label             TEXT        NOT NULL,
    description       TEXT,

    -- Embedding of the narrative centroid (JSON float array placeholder)
    embedding         TEXT,
    -- Algorithm used to form this cluster, e.g. "hdbscan", "llm_grouping"
    clustering_method TEXT,
    -- Rate of new supporting claims per day (updated by clustering job)
    velocity_score    REAL        DEFAULT 0.0,

    first_seen_at     TIMESTAMPTZ,
    last_seen_at      TIMESTAMPTZ,

    -- Event that initiated or significantly accelerated this narrative
    trigger_event_id  INTEGER     REFERENCES kg_events(id) ON DELETE SET NULL,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_kg_narratives_last_seen      ON kg_narratives(last_seen_at);
CREATE INDEX idx_kg_narratives_velocity       ON kg_narratives(velocity_score);
CREATE INDEX idx_kg_narratives_trigger_event  ON kg_narratives(trigger_event_id)
    WHERE trigger_event_id IS NOT NULL;


-- ── Narrative ↔ Claim (M:M) ──────────────────────────────────────────────────

CREATE TABLE kg_narrative_claims (
    narrative_id INTEGER     NOT NULL REFERENCES kg_narratives(id) ON DELETE CASCADE,
    claim_id     INTEGER     NOT NULL REFERENCES kg_claims(id)     ON DELETE CASCADE,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (narrative_id, claim_id)
);

CREATE INDEX idx_kg_narrative_claims_claim ON kg_narrative_claims(claim_id);


-- ── Edges (explicit typed graph relationships) ────────────────────────────────
-- Polymorphic: from_type/to_type identify the node table; from_id/to_id are
-- the PKs within that table.  No FK constraints by design — a polymorphic FK
-- cannot be expressed in standard SQL.  The application layer owns referential
-- integrity.  Use this table for analytical traversals, not structural queries
-- (use the M:M join tables above for those).

CREATE TABLE kg_edges (
    id                SERIAL               PRIMARY KEY,
    from_type         kg_node_type         NOT NULL,
    from_id           INTEGER              NOT NULL,
    to_type           kg_node_type         NOT NULL,
    to_id             INTEGER              NOT NULL,
    relationship_type kg_relationship_type NOT NULL,
    confidence_score  REAL                 DEFAULT NULL
                          CHECK (
                              confidence_score IS NULL OR
                              (confidence_score >= 0.0 AND confidence_score <= 1.0)
                          ),
    created_at        TIMESTAMPTZ          NOT NULL DEFAULT NOW(),

    -- Prevent duplicate edges of the same typed relationship.
    UNIQUE (from_type, from_id, to_type, to_id, relationship_type)
);

-- Outbound traversal: "what does node X point to?"
CREATE INDEX idx_kg_edges_from ON kg_edges(from_type, from_id);
-- Inbound traversal: "what points at node Y?"
CREATE INDEX idx_kg_edges_to   ON kg_edges(to_type,   to_id);
-- Filter by relationship type across all nodes.
CREATE INDEX idx_kg_edges_rel  ON kg_edges(relationship_type);
