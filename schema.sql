-- =====================================================================
-- TamilHeritage-GPT  |  Level 1: PostgreSQL master schema
-- PostgreSQL = "truth" (sites, locations, aliases, sources, facts, logs)
-- Vector DB  = "search index" (chunks + embeddings), linked by site_id
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy alias matching (Level 3)

-- ---------------------------------------------------------------------
-- 1. LOCATIONS  (mirrors LGD: State > District > Sub-district(Taluk) ; Local bodies)
--    Revenue hierarchy and local-body hierarchy are DIFFERENT trees in LGD,
--    so a site points to both (subdistrict_id + local_body_id).
-- ---------------------------------------------------------------------
CREATE TYPE location_level AS ENUM ('state','district','subdistrict','local_body');
CREATE TYPE local_body_kind AS ENUM (
  'municipal_corporation','municipality','town_panchayat','village_panchayat','other');

CREATE TABLE locations (
  location_id     SERIAL PRIMARY KEY,
  parent_id       INT REFERENCES locations(location_id),
  level           location_level NOT NULL,
  name_en         TEXT NOT NULL,
  name_ta         TEXT,
  local_body_type local_body_kind,          -- only when level = 'local_body'
  lgd_code        INT UNIQUE,               -- official LGD code (fill from lgdirectory.gov.in)
  verified        BOOLEAN DEFAULT FALSE
);
CREATE INDEX ON locations(parent_id);

-- ---------------------------------------------------------------------
-- 2. CATEGORIES  (controlled vocabulary – no free text)
-- ---------------------------------------------------------------------
CREATE TABLE categories (
  code      TEXT PRIMARY KEY,
  label_en  TEXT NOT NULL,
  label_ta  TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 3. SITES  (one row per real-world place = entity-centric)
-- ---------------------------------------------------------------------
CREATE TYPE site_status AS ENUM ('verified','pending','rejected');
CREATE TYPE trust_level AS ENUM ('official','multi_source','single_source','unverified');

CREATE TABLE sites (
  site_id          TEXT PRIMARY KEY,                -- e.g. TN-TNJ-0001  (state-district-seq)
  parent_site_id   TEXT REFERENCES sites(site_id),  -- complex -> component (Fort St. George -> Clive's House)
  name_en          TEXT NOT NULL,
  name_ta          TEXT,
  primary_category TEXT NOT NULL REFERENCES categories(code),
  categories       TEXT[] NOT NULL,                 -- multi-tag (all codes from categories)
  district_id      INT NOT NULL REFERENCES locations(location_id),
  subdistrict_id   INT REFERENCES locations(location_id),
  local_body_id    INT REFERENCES locations(location_id),
  lat              NUMERIC(9,6),
  lon              NUMERIC(9,6),
  period           TEXT,                            -- e.g. '11th century CE'
  dynasty          TEXT,
  status           site_status NOT NULL DEFAULT 'pending',
  trust            trust_level NOT NULL DEFAULT 'unverified',
  origin           TEXT NOT NULL DEFAULT 'curated', -- 'curated' | 'web_consensus'
  created_at       TIMESTAMPTZ DEFAULT now(),
  updated_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON sites USING GIN (categories);       -- "categories @> '{temple}'"
CREATE INDEX ON sites(district_id);
CREATE INDEX ON sites(parent_site_id);

-- ---------------------------------------------------------------------
-- 4. ALIASES  (Big Temple = Peruvudaiyar Kovil = Tanjore Temple …)
-- ---------------------------------------------------------------------
CREATE TABLE site_aliases (
  alias_id    SERIAL PRIMARY KEY,
  site_id     TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
  alias       TEXT NOT NULL,
  lang        TEXT NOT NULL,                        -- 'en' | 'ta' | 'kn' | 'ta-Latn' (Tanglish)
  alias_norm  TEXT GENERATED ALWAYS AS (lower(alias)) STORED,
  UNIQUE (site_id, alias)
);
CREATE INDEX ON site_aliases USING GIN (alias_norm gin_trgm_ops);

-- ---------------------------------------------------------------------
-- 5. SOURCES  (where every piece of text came from)
-- ---------------------------------------------------------------------
CREATE TABLE sources (
  source_id    SERIAL PRIMARY KEY,
  url          TEXT UNIQUE NOT NULL,
  domain       TEXT NOT NULL,
  tier         CHAR(1) NOT NULL CHECK (tier IN ('A','B','C')),  -- A official, B reference, C other
  fetched_at   TIMESTAMPTZ DEFAULT now(),
  content_hash TEXT
);

-- ---------------------------------------------------------------------
-- 6. SITE_FACTS  (field-level facts with provenance – used by Level 4/5)
-- ---------------------------------------------------------------------
CREATE TABLE site_facts (
  fact_id    SERIAL PRIMARY KEY,
  site_id    TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
  field      TEXT NOT NULL,                        -- builder | built_year | deity | festival …
  value      TEXT NOT NULL,
  lang       TEXT NOT NULL DEFAULT 'en',
  source_id  INT REFERENCES sources(source_id),
  score      NUMERIC(4,3),                         -- consensus score 0..1
  status     site_status NOT NULL DEFAULT 'pending'
);
CREATE INDEX ON site_facts(site_id, field);

-- ---------------------------------------------------------------------
-- 7. CHUNK_REGISTRY  (mirror of what is inside the vector DB -> sync, audit, rollback)
-- ---------------------------------------------------------------------
CREATE TABLE chunk_registry (
  chunk_id     UUID PRIMARY KEY,                   -- same id as the vector DB point
  site_id      TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
  section      TEXT NOT NULL,                      -- overview|history|architecture|festivals|visitor_info|how_to_reach|collections
  lang         TEXT NOT NULL,
  source_id    INT REFERENCES sources(source_id),
  content_hash TEXT NOT NULL,                      -- sha256(text) -> change detection
  token_count  INT,
  origin       TEXT NOT NULL DEFAULT 'curated',
  created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON chunk_registry(site_id);

-- =====================================================================
-- USERS & CHAT HISTORY
-- =====================================================================

-- ---------------------------------------------------------------------
-- 8. QUESTION_TYPES  (what kind of question was asked -> analytics + routing)
-- ---------------------------------------------------------------------
CREATE TABLE question_types (
  code        TEXT PRIMARY KEY,
  label_en    TEXT NOT NULL,
  label_ta    TEXT NOT NULL,
  description TEXT
);

-- ---------------------------------------------------------------------
-- 9. USERS  (registered or guest accounts)
-- ---------------------------------------------------------------------
CREATE TYPE user_role   AS ENUM ('user','reviewer','admin');
CREATE TYPE auth_kind   AS ENUM ('google','email','phone_otp','guest');

CREATE TABLE users (
  user_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_provider    auth_kind NOT NULL,
  email            TEXT UNIQUE,
  phone            TEXT UNIQUE,
  password_hash    TEXT,                               -- only for 'email' login (bcrypt/argon2), never plain text
  display_name     TEXT,
  preferred_lang   TEXT NOT NULL DEFAULT 'en',          -- en | ta | kn | ta-Latn
  home_district_id INT REFERENCES locations(location_id),
  user_type        TEXT,                               -- student | tourist | devotee | researcher | other (survey)
  role             user_role NOT NULL DEFAULT 'user',
  consent_logging  BOOLEAN NOT NULL DEFAULT FALSE,     -- may we use their chats for research?
  created_at       TIMESTAMPTZ DEFAULT now(),
  last_active_at   TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- 10. CHAT_SESSIONS  (one conversation = many messages)
-- ---------------------------------------------------------------------
CREATE TABLE chat_sessions (
  session_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  title           TEXT,                                -- auto: first question, shortened
  client          TEXT NOT NULL DEFAULT 'web',         -- web | android | ios
  started_at      TIMESTAMPTZ DEFAULT now(),
  last_message_at TIMESTAMPTZ DEFAULT now(),
  is_archived     BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ON chat_sessions(user_id, last_message_at DESC);

-- ---------------------------------------------------------------------
-- 11. CHAT_MESSAGES  (the actual chat history: user questions + bot answers)
-- ---------------------------------------------------------------------
CREATE TYPE msg_role AS ENUM ('user','assistant');

CREATE TABLE chat_messages (
  message_id     BIGSERIAL PRIMARY KEY,
  session_id     UUID NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
  role           msg_role NOT NULL,
  content        TEXT NOT NULL,
  lang           TEXT,                                 -- detected (user) / generated (assistant)
  question_type  TEXT REFERENCES question_types(code), -- only for role='user'
  resolved_site  TEXT REFERENCES sites(site_id),       -- which site the question was about
  reply_to       BIGINT REFERENCES chat_messages(message_id),  -- assistant -> its user question
  created_at     TIMESTAMPTZ DEFAULT now(),
  CHECK (role = 'user' OR reply_to IS NOT NULL)
);
CREATE INDEX ON chat_messages(session_id, created_at);
CREATE INDEX ON chat_messages(question_type);

-- ---------------------------------------------------------------------
-- 12. MESSAGE_CITATIONS  (which chunks were used to write each answer)
-- ---------------------------------------------------------------------
CREATE TABLE message_citations (
  message_id  BIGINT NOT NULL REFERENCES chat_messages(message_id) ON DELETE CASCADE,
  chunk_id    UUID   NOT NULL REFERENCES chunk_registry(chunk_id),
  rank        SMALLINT NOT NULL,
  score       NUMERIC(5,4),
  PRIMARY KEY (message_id, chunk_id)
);

-- ---------------------------------------------------------------------
-- 13. MESSAGE_FEEDBACK  (thumbs up/down + survey -> accuracy for the journal)
-- ---------------------------------------------------------------------
CREATE TABLE message_feedback (
  message_id  BIGINT NOT NULL REFERENCES chat_messages(message_id) ON DELETE CASCADE,
  user_id     UUID   NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  rating      SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
  reason      TEXT,                                    -- wrong_fact | wrong_place | wrong_language | incomplete | other
  comment     TEXT,
  created_at  TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (message_id, user_id)
);

-- ---------------------------------------------------------------------
-- 14. QUERY_LOGS  (pipeline trace for every user message: path, scores, latency)
-- ---------------------------------------------------------------------
CREATE TABLE query_logs (
  log_id        BIGSERIAL PRIMARY KEY,
  message_id    BIGINT UNIQUE REFERENCES chat_messages(message_id) ON DELETE CASCADE,
  query_text    TEXT NOT NULL,
  query_lang    TEXT,                              -- ta | en | ta-Latn | kn
  rewritten_en  TEXT,                              -- query after Tanglish/translation rewrite
  resolved_site TEXT REFERENCES sites(site_id),
  path          TEXT,                              -- db | web | rejected
  top_score     NUMERIC(5,4),
  chunk_ids     UUID[],
  latency_ms    INT,
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------
-- VIEW: who asks which type of question
-- ---------------------------------------------------------------------
CREATE VIEW user_question_stats AS
SELECT u.user_id, u.display_name, u.user_type, m.question_type,
       count(*) AS questions, max(m.created_at) AS last_asked
FROM chat_messages m
JOIN chat_sessions s ON s.session_id = m.session_id
JOIN users u         ON u.user_id    = s.user_id
WHERE m.role = 'user'
GROUP BY u.user_id, u.display_name, u.user_type, m.question_type;
