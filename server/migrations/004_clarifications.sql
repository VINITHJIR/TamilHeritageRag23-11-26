-- Migration: 004_clarifications.sql
-- Adds tables for clarification flow and candidate cache

CREATE TABLE clarifications (
    clarification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    asked_message_id BIGINT NOT NULL,
    entity_name TEXT NOT NULL,
    entity_name_ta TEXT,
    question_type TEXT NOT NULL,
    original_query TEXT NOT NULL,
    candidates JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open','resolved','cancelled','expired')),
    resolved_option JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX idx_clarifications_session ON clarifications(session_id);
CREATE INDEX idx_clarifications_status ON clarifications(status);

CREATE TABLE candidate_cache (
    candidate_key TEXT PRIMARY KEY,
    candidates JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_days INT NOT NULL DEFAULT 30
);

-- Optional: trigger to auto-expire cache entries (or handled in application)
