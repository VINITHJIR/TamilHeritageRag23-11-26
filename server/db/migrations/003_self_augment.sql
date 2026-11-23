-- =====================================================================
-- TamilHeritage-GPT  |  Level 2: IC³-WB Self-Augmentation Schema
-- Additive only: no DROP, no destructive changes.
-- Idempotent: uses IF NOT EXISTS / ON CONFLICT DO NOTHING.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. SOURCE_DOMAINS (Domain reputation, tiering & beta distribution)
--    reliability = alpha / (alpha + beta)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_domains (
  domain      TEXT PRIMARY KEY,
  tier        CHAR(1) NOT NULL CHECK (tier IN ('A','B','C')),
  prior       NUMERIC NOT NULL,
  alpha       NUMERIC NOT NULL DEFAULT 2,
  beta        NUMERIC NOT NULL DEFAULT 1,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed Tier A (official/governmental heritage bodies) and Tier B (reference/media)
INSERT INTO source_domains (domain, tier, prior, alpha, beta) VALUES
  ('asi.nic.in',                 'A', 1.0,  2, 1),
  ('asichennai.gov.in',          'A', 1.0,  2, 1),
  ('tnarch.gov.in',              'A', 1.0,  2, 1),
  ('hrce.tn.gov.in',             'A', 1.0,  2, 1),
  ('tamilnadutourism.tn.gov.in', 'A', 1.0,  2, 1),
  ('whc.unesco.org',             'A', 1.0,  2, 1),
  ('ignca.gov.in',               'A', 1.0,  2, 1),
  ('tamildigitallibrary.in',     'A', 1.0,  2, 1),
  ('abhilekh-patal.in',          'A', 1.0,  2, 1),
  ('bharatshri.asi.gov.in',      'A', 1.0,  2, 1),
  ('wikipedia.org',              'B', 0.6,  2, 1),
  ('wikidata.org',               'B', 0.6,  2, 1),
  ('thehindu.com',               'B', 0.6,  2, 1),
  ('newindianexpress.com',       'B', 0.6,  2, 1),
  ('timesofindia.indiatimes.com','B', 0.6,  2, 1)
ON CONFLICT (domain) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. AUGMENT_JOBS (Execution tracker for self-augmentation runs)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS augment_jobs (
  job_id           UUID PRIMARY KEY,
  message_id       BIGINT REFERENCES chat_messages(message_id) ON DELETE SET NULL,
  candidate_key    TEXT NOT NULL,
  site_id          TEXT REFERENCES sites(site_id) ON DELETE SET NULL,
  query            TEXT NOT NULL,
  entity_name      TEXT NOT NULL,
  entity_name_ta   TEXT,
  place_name       TEXT,
  district_id      INT REFERENCES locations(location_id),
  lang             TEXT,
  status           TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running','verified','rejected_nonexistent','failed','skipped_budget')),
  decision_reason  TEXT,
  config           JSONB NOT NULL DEFAULT '{}'::jsonb,
  gemini_calls     INT NOT NULL DEFAULT 0,
  gemini_tokens    INT NOT NULL DEFAULT 0,
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ,
  ingestion_run_id BIGINT REFERENCES ingestion_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS augment_jobs_candidate_key_idx ON augment_jobs(candidate_key);
CREATE INDEX IF NOT EXISTS augment_jobs_site_id_idx ON augment_jobs(site_id);
CREATE INDEX IF NOT EXISTS augment_jobs_status_idx ON augment_jobs(status);

-- ---------------------------------------------------------------------
-- 3. AUGMENT_SOURCES (Web sources fetched per job; excerpt <= 500 chars)
--    Never store full page text in the database.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS augment_sources (
  id               BIGSERIAL PRIMARY KEY,
  job_id           UUID NOT NULL REFERENCES augment_jobs(job_id) ON DELETE CASCADE,
  url              TEXT NOT NULL,
  final_url        TEXT,
  domain           TEXT NOT NULL,
  tier             CHAR(1) NOT NULL CHECK (tier IN ('A','B','C')),
  fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  http_status      INT,
  lang             TEXT,
  text_sha256      TEXT,
  minhash          JSONB,
  cluster_key      TEXT,
  excerpt          TEXT CHECK (excerpt IS NULL OR length(excerpt) <= 500),
  mentions_entity  BOOLEAN NOT NULL DEFAULT FALSE,
  mentions_place   BOOLEAN NOT NULL DEFAULT FALSE,
  used             BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS augment_sources_job_id_idx ON augment_sources(job_id);
CREATE INDEX IF NOT EXISTS augment_sources_cluster_key_idx ON augment_sources(cluster_key);

-- ---------------------------------------------------------------------
-- 4. EVIDENCE_LEDGER (Extracted claims and verbatim quotes across sources)
--    candidate_key = normalized(entity_name) + '|' + district_id
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence_ledger (
  id                  BIGSERIAL PRIMARY KEY,
  candidate_key       TEXT NOT NULL,
  site_id             TEXT REFERENCES sites(site_id) ON DELETE SET NULL,
  field               TEXT NOT NULL,
  raw_value           TEXT,
  norm_value          TEXT,
  url                 TEXT NOT NULL,
  domain              TEXT NOT NULL,
  tier                CHAR(1) NOT NULL CHECK (tier IN ('A','B','C')),
  cluster_key         TEXT,
  evidence_quote      TEXT,
  quote_found         BOOLEAN NOT NULL DEFAULT FALSE,
  extractor_agreement BOOLEAN NOT NULL DEFAULT FALSE,
  lang                TEXT,
  job_id              UUID REFERENCES augment_jobs(job_id) ON DELETE CASCADE,
  observed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evidence_ledger_candidate_key_idx ON evidence_ledger(candidate_key, field);
CREATE INDEX IF NOT EXISTS evidence_ledger_site_id_idx ON evidence_ledger(site_id, field);
CREATE INDEX IF NOT EXISTS evidence_ledger_job_id_idx ON evidence_ledger(job_id);

-- ---------------------------------------------------------------------
-- 5. FIELD_DECISIONS (Deterministic consensus decisions per site field)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS field_decisions (
  id           BIGSERIAL PRIMARY KEY,
  site_id      TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
  field        TEXT NOT NULL,
  decision     TEXT NOT NULL CHECK (decision IN ('verified','conflict','rejected','expired')),
  value        TEXT,
  competing    JSONB NOT NULL DEFAULT '[]'::jsonb,
  support      NUMERIC,
  n_clusters   INT NOT NULL DEFAULT 0,
  has_tier_a   BOOLEAN NOT NULL DEFAULT FALSE,
  rule_fired   TEXT,
  attempts     INT NOT NULL DEFAULT 1,
  job_id       UUID REFERENCES augment_jobs(job_id) ON DELETE SET NULL,
  decided_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (site_id, field)
);
CREATE INDEX IF NOT EXISTS field_decisions_site_id_idx ON field_decisions(site_id);

-- ---------------------------------------------------------------------
-- 6. SITE_FACTS & SITES EXTENSIONS
-- ---------------------------------------------------------------------
ALTER TABLE site_facts ADD COLUMN IF NOT EXISTS decision TEXT CHECK (decision IN ('verified','conflict'));

-- ---------------------------------------------------------------------
-- 7. QUERY_LOGS EXTENSIONS (Detailed augmentation path tracking & cost)
-- ---------------------------------------------------------------------
ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS path_detail JSONB;
ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS gemini_calls INT DEFAULT 0;
