-- ProFinder pipeline: process-state tables (doc 01 section 6). Own schema `ingest`; no app table is touched.
-- Idempotent: safe to run twice. Do NOT edit after it has been applied anywhere (init-db verifies the checksum);
-- add 002_*.sql instead.
CREATE SCHEMA IF NOT EXISTS ingest;

CREATE TABLE IF NOT EXISTS ingest.run (
    run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        text NOT NULL CHECK (kind IN ('registry','discover','crawl','extract','release','load')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    git_sha     text,
    config_hash text,
    params      jsonb NOT NULL DEFAULT '{}'::jsonb,
    stats       jsonb NOT NULL DEFAULT '{}'::jsonb,
    status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','failed','partial'))
);

CREATE TABLE IF NOT EXISTS ingest.target (
    target_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    university_name text NOT NULL,
    university_id   text,
    kind            text NOT NULL CHECK (kind IN ('faculty_list','faculty_profile','admissions','programs','scholarship','registry')),
    url             text NOT NULL,
    discovered_by   text NOT NULL CHECK (discovered_by IN ('seed','link_follow','search','manual')),
    parser          text CHECK (parser IN ('css','llm','api')),
    parser_config   jsonb NOT NULL DEFAULT '{}'::jsonb,
    robots_status   text NOT NULL DEFAULT 'unknown' CHECK (robots_status IN ('allowed','disallowed','unknown')),
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ok','failed','blocked','no_page_found')),
    fail_count      integer NOT NULL DEFAULT 0,
    last_error      text,
    last_fetch_at   timestamptz,
    next_fetch_at   timestamptz,
    UNIQUE (url, kind)
);
CREATE INDEX IF NOT EXISTS target_university_idx ON ingest.target (university_name, kind);
CREATE INDEX IF NOT EXISTS target_next_fetch_idx ON ingest.target (next_fetch_at);

CREATE TABLE IF NOT EXISTS ingest.page (
    page_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id    uuid REFERENCES ingest.target (target_id),
    url          text NOT NULL,
    final_url    text,
    url_hash     char(32) NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    http_status  integer,
    render_mode  text CHECK (render_mode IN ('static','playwright')),
    encoding     text,
    content_hash char(32),
    markdown     text,
    error        text
);
CREATE INDEX IF NOT EXISTS page_url_hash_idx ON ingest.page (url_hash, fetched_at DESC);
CREATE INDEX IF NOT EXISTS page_target_idx ON ingest.page (target_id);

CREATE TABLE IF NOT EXISTS ingest.release (
    release_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    manifest    jsonb NOT NULL DEFAULT '{}'::jsonb,
    status      text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','loaded','abandoned')),
    approved_by text,
    approved_at timestamptz,
    loaded_to   text,            -- host name only, never a full URL
    loaded_at   timestamptz
);

CREATE TABLE IF NOT EXISTS ingest.candidate (
    candidate_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            uuid NOT NULL REFERENCES ingest.run (run_id),
    entity_type       text NOT NULL CHECK (entity_type IN ('university','professor','program','scholarship','publication')),
    natural_key       char(32) NOT NULL,
    university_name   text,
    payload           jsonb NOT NULL,
    page_id           uuid REFERENCES ingest.page (page_id),
    source_url        text NOT NULL,
    evidence          jsonb NOT NULL DEFAULT '[]'::jsonb,
    extractor         text NOT NULL CHECK (extractor IN ('css','llm','api','manual')),
    model             text,
    prompt_version    text,
    confidence        real CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    checks            jsonb NOT NULL DEFAULT '[]'::jsonb,
    proposed_status   text NOT NULL CHECK (proposed_status IN ('verified','needs_review','unverified','outdated')),
    decision          text NOT NULL CHECK (decision IN ('auto_accept','needs_review','rejected','human_approved','human_rejected')),
    reject_reason     text,
    matched_entity_id text,
    action            text CHECK (action IN ('insert','enrich','propose','noop')),
    diff              jsonb NOT NULL DEFAULT '[]'::jsonb,
    reviewer          text,
    reviewed_at       timestamptz,
    review_note       text,
    release_id        uuid REFERENCES ingest.release (release_id),
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS candidate_natural_key_idx ON ingest.candidate (entity_type, natural_key);
CREATE INDEX IF NOT EXISTS candidate_run_idx ON ingest.candidate (run_id);
CREATE INDEX IF NOT EXISTS candidate_decision_idx ON ingest.candidate (entity_type, decision);
CREATE INDEX IF NOT EXISTS candidate_release_idx ON ingest.candidate (release_id);
