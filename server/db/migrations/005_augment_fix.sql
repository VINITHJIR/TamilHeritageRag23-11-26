-- =====================================================================
-- TamilHeritage-GPT  |  Migration 005: Augment Fix, Message Traces & Option Choices
-- Additive and idempotent. Never edit old migrations.
-- =====================================================================

-- 1. MESSAGE_TRACES
CREATE TABLE IF NOT EXISTS message_traces (
  trace_id    BIGSERIAL PRIMARY KEY,
  message_id  BIGINT REFERENCES chat_messages(message_id) ON DELETE CASCADE,
  stage       TEXT NOT NULL,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  duration_ms INT,
  input       JSONB,
  output      JSONB,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS message_traces_message_id_idx ON message_traces(message_id);
CREATE INDEX IF NOT EXISTS message_traces_stage_idx ON message_traces(stage);

-- 2. AUGMENT_JOBS status constraint update for allowed statuses
ALTER TABLE augment_jobs DROP CONSTRAINT IF EXISTS augment_jobs_status_check;
ALTER TABLE augment_jobs ADD CONSTRAINT augment_jobs_status_check CHECK (
  status IN (
    'running',
    'verified',
    'provisional',
    'needs_clarification',
    'needs_choice',
    'out_of_scope',
    'rejected_nonexistent',
    'quota_exhausted',
    'failed',
    'skipped_budget'
  )
);

-- 3. SITES completeness column
ALTER TABLE sites ADD COLUMN IF NOT EXISTS completeness TEXT
  CHECK (completeness IS NULL OR completeness IN ('existence_only', 'partial', 'complete'));

-- 4. OPTION_CHOICES table for user selection on conflicting values (Phase 4)
CREATE TABLE IF NOT EXISTS option_choices (
  choice_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id   BIGINT REFERENCES chat_messages(message_id) ON DELETE CASCADE,
  user_id      UUID REFERENCES users(user_id) ON DELETE CASCADE,
  field        TEXT NOT NULL,
  chosen_value TEXT NOT NULL,
  options      JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS option_choices_message_id_idx ON option_choices(message_id);

-- 5. FIELD_DECISIONS decision constraint update for unconfirmed (Rule 3.3)
ALTER TABLE field_decisions DROP CONSTRAINT IF EXISTS field_decisions_decision_check;
ALTER TABLE field_decisions ADD CONSTRAINT field_decisions_decision_check CHECK (
  decision IN ('verified', 'conflict', 'unconfirmed', 'rejected', 'expired')
);
