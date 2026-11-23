-- =====================================================================
-- TamilHeritage-GPT  |  Level 1B: ingestion bookkeeping + schema extensions
-- Additive only: no DROP, no data deletion, no change to existing rows'
-- values (except advancing one sequence, see section 0).
-- Idempotent: every statement uses IF NOT EXISTS / ON CONFLICT, so a manual
-- re-run is harmless (migrate.py additionally runs it only once).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. Repair: seed.sql inserted locations with explicit ids (max 26) without
--    advancing the SERIAL, so nextval() would return 1 and collide.
--    setval only moves the counter forward; it never touches rows.
-- ---------------------------------------------------------------------
SELECT setval(pg_get_serial_sequence('locations', 'location_id'),
              GREATEST((SELECT COALESCE(max(location_id), 0) FROM locations), 1),
              (SELECT count(*) > 0 FROM locations));

-- ---------------------------------------------------------------------
-- 1. New categories (controlled vocabulary for ASI / Wikidata heritage types)
-- ---------------------------------------------------------------------
INSERT INTO categories (code, label_en, label_ta) VALUES
 ('church',            'Church',                       'தேவாலயம்'),
 ('mosque',            'Mosque',                       'பள்ளிவாசல்'),
 ('dargah',            'Dargah',                       'தர்கா'),
 ('jain_site',         'Jain site',                    'சமணத் தலம்'),
 ('tomb_cemetery',     'Tomb / Cemetery',              'கல்லறை / இடுகாடு'),
 ('megalithic_site',   'Megalithic site',              'பெருங்கற்காலத் தளம்'),
 ('inscription',       'Inscription',                  'கல்வெட்டு'),
 ('cave',              'Cave',                         'குகை'),
 ('colonial_building', 'Colonial building',            'காலனித்துவக் கட்டடம்'),
 ('step_well_tank',    'Step well / Temple tank',      'படிக்கிணறு / குளம்'),
 ('other_monument',    'Other monument',               'பிற நினைவுச்சின்னம்')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. SITE_SOURCE_KEYS  (external key -> stable site_id)
--    e.g. ('asi', 'asi_chennai:monument:12') -> 'TN-CGL-0001'
--    Re-runs look the key up here first, so site_ids never change.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_source_keys (
  source      TEXT NOT NULL,                       -- asi | wikidata | lgd | curated ...
  source_key  TEXT NOT NULL,
  site_id     TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, source_key)
);
CREATE INDEX IF NOT EXISTS site_source_keys_site_id_idx ON site_source_keys(site_id);

-- ---------------------------------------------------------------------
-- 3. INGESTION_RUNS  (one row per pipeline step execution -> provenance for the paper)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
  run_id          BIGSERIAL PRIMARY KEY,
  step            TEXT NOT NULL,                   -- lgd | asi | classify | group | wikidata | wikipedia | chunks | load
  source          TEXT,
  input_file      TEXT,                            -- path relative to server/data/raw
  input_sha256    TEXT,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ,
  status          TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running','success','failed','skipped')),
  rows_in         INT NOT NULL DEFAULT 0,
  inserted        INT NOT NULL DEFAULT 0,
  updated         INT NOT NULL DEFAULT 0,
  skipped         INT NOT NULL DEFAULT 0,
  sent_to_review  INT NOT NULL DEFAULT 0,
  error           TEXT
);
CREATE INDEX IF NOT EXISTS ingestion_runs_step_idx   ON ingestion_runs(step, started_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_runs_sha256_idx ON ingestion_runs(input_sha256);

-- ---------------------------------------------------------------------
-- 4. REVIEW_QUEUE  (human-in-the-loop decisions: category | match | parent | location)
--    status: open (needs a human) | auto (decided automatically, kept for audit)
--            | accepted | rejected (resolved by a reviewer)
--    UNIQUE (kind, source, source_key) lets the pipeline upsert, so running it
--    twice does not duplicate review items (idempotency requirement, Phase 6).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_queue (
  review_id    BIGSERIAL PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('category','match','parent','location')),
  source       TEXT NOT NULL,
  source_key   TEXT NOT NULL,
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- the incoming record
  candidates   JSONB NOT NULL DEFAULT '[]'::jsonb,   -- ranked options with scores
  reason       TEXT,
  status       TEXT NOT NULL DEFAULT 'open'
               CHECK (status IN ('open','auto','accepted','rejected')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_by  UUID REFERENCES users(user_id),
  resolved_at  TIMESTAMPTZ,
  UNIQUE (kind, source, source_key)
);
CREATE INDEX IF NOT EXISTS review_queue_open_idx ON review_queue(kind, status);

-- ---------------------------------------------------------------------
-- 5. LOCATIONS.code3  (3-letter district code used in site_id, e.g. TNJ)
--    Filled by app/ingestion/ids.py; unique among districts.
-- ---------------------------------------------------------------------
ALTER TABLE locations ADD COLUMN IF NOT EXISTS code3 TEXT
  CHECK (code3 IS NULL OR code3 ~ '^[A-Z]{3}$');
CREATE UNIQUE INDEX IF NOT EXISTS locations_district_code3_uidx
  ON locations(code3) WHERE level = 'district' AND code3 IS NOT NULL;
CREATE INDEX IF NOT EXISTS locations_level_parent_idx ON locations(level, parent_id);

-- ---------------------------------------------------------------------
-- 6. SITES external identifiers
-- ---------------------------------------------------------------------
ALTER TABLE sites ADD COLUMN IF NOT EXISTS wikidata_qid TEXT UNIQUE
  CHECK (wikidata_qid IS NULL OR wikidata_qid ~ '^Q[0-9]+$');
ALTER TABLE sites ADD COLUMN IF NOT EXISTS asi_id TEXT;
CREATE INDEX IF NOT EXISTS sites_asi_id_idx ON sites(asi_id);

-- ---------------------------------------------------------------------
-- 7. SOURCES.license  (attribution, e.g. 'CC BY-SA 4.0, Wikipedia contributors',
--    'CC0 (Wikidata)'). Needed by enrich_wikipedia.py / enrich_wikidata.py.
-- ---------------------------------------------------------------------
ALTER TABLE sources ADD COLUMN IF NOT EXISTS license TEXT;
