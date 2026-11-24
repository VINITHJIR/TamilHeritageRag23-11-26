"""Phase 2 verification tests (Rules 2.1 & 2.2).

Tests:
1. "masani amman temple" (no place) -> needs_clarification with Pollachi option (Rule 2.2).
2. User replies "Pollachi" -> resolves clarification, continues with Pollachi/Coimbatore place context (Rule 2.2).
3. Unrelated reply -> cancels clarification, processes as new query (Rule 2.2).
4. Retry immediately after failed/rejected works without 24h budget block (Rule 2.1).
5. Reuse verified site within 24h without web calls, returning VERIFIED_REUSED (Rule 2.1).
6. '|unknown' candidate keys are never reused (Rule 2.1).
7. Gemini quota exhausted returns status quota_exhausted and polite busy message (Rule 2.1).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import uuid
import pytest
from starlette.testclient import TestClient
from sqlalchemy import text as sa_text

from app.main import app
from app.db.postgres import SessionLocal
from app.services.spec_config import DecisionCode, GEMINI_DAILY_BUDGET
from app.services.augment_orchestrator import run_augmentation_pipeline, AugmentResult
from app.services.clarifications import get_open_clarification

client = TestClient(app)


def test_rule_2_2_masani_amman_no_place_triggers_needs_clarification():
    """Rule 2.2: 'masani amman temple' without place triggers identity clarification with Pollachi option."""
    guest_res = client.post("/api/users/guest")
    assert guest_res.status_code == 200
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "are you know about masani amman temple"})
    assert res.status_code == 200
    data = res.json()
    asst_msg = data["assistant_message"]

    # Verify path is needs_clarification
    assert asst_msg["path"] == "needs_clarification"
    # Verify candidate options mention Pollachi
    assert "pollachi" in asst_msg["content"].lower()

    with SessionLocal() as db:
        # Verify clarification record was created with status 'open'
        open_clar = get_open_clarification(db, session_id)
        assert open_clar is not None
        assert open_clar.status == "open"
        assert open_clar.entity_name is not None
        assert len(open_clar.candidates) >= 1
        has_pollachi = any("pollachi" in (c.get("locality", "") + c.get("label", "")).lower() for c in open_clar.candidates)
        assert has_pollachi

        # Verify query_logs entry
        user_mid = data["user_message"]["message_id"]
        log_row = db.execute(
            sa_text("SELECT path, path_detail FROM query_logs WHERE message_id = :mid"),
            {"mid": user_mid},
        ).mappings().fetchone()
        assert log_row is not None
        assert log_row["path"] == "needs_clarification"
        assert log_row["path_detail"]["status"] == "needs_clarification"
        assert log_row["path_detail"]["decision_code"] == DecisionCode.NEEDS_CLARIFICATION.value


def test_rule_2_2_reply_pollachi_resolves_clarification():
    """Rule 2.2: Replying 'Pollachi' matches the open candidate, resolves clarification, and enriches query."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    # 1. Ask ambiguous query
    res1 = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "masani amman temple"})
    assert res1.json()["assistant_message"]["path"] == "needs_clarification"

    # 2. Answer with "Pollachi"
    res2 = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "Pollachi"})
    assert res2.status_code == 200
    data2 = res2.json()

    with SessionLocal() as db:
        # Verify clarification is marked resolved
        clar_row = db.execute(
            sa_text("SELECT status, resolved_option FROM clarifications WHERE session_id = :sid ORDER BY created_at DESC LIMIT 1"),
            {"sid": session_id},
        ).mappings().fetchone()
        assert clar_row is not None
        assert clar_row["status"] == "resolved"
        assert "pollachi" in str(clar_row["resolved_option"]).lower()


def test_rule_2_2_unrelated_reply_cancels_clarification():
    """Rule 2.2: Unrelated reply cancels clarification and processes as a new fresh query."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    # 1. Ask ambiguous query
    res1 = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "masani amman temple"})
    assert res1.json()["assistant_message"]["path"] == "needs_clarification"

    # 2. Send unrelated message: "what is python"
    res2 = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "what is python"})
    assert res2.status_code == 200
    data2 = res2.json()

    # Should be processed as out_of_scope
    assert data2["assistant_message"]["path"] == "out_of_scope"

    with SessionLocal() as db:
        clar_row = db.execute(
            sa_text("SELECT status FROM clarifications WHERE session_id = :sid ORDER BY created_at DESC LIMIT 1"),
            {"sid": session_id},
        ).mappings().fetchone()
        assert clar_row is not None
        assert clar_row["status"] == "cancelled"


def test_rule_2_1_retry_immediately_after_failed_rejected_without_24h_block():
    """Rule 2.1: Failed or rejected jobs NEVER block an immediate retry (no 24h budget block)."""
    candidate_key = "test_retry_temple|58"
    with SessionLocal() as db:
        # Clean previous test entries
        db.execute(sa_text("DELETE FROM augment_jobs WHERE candidate_key = :ck"), {"ck": candidate_key})
        # Insert a failed job from 10 minutes ago
        db.execute(sa_text("""
            INSERT INTO augment_jobs (
                job_id, candidate_key, status, decision_reason, gemini_calls, gemini_tokens,
                started_at, finished_at, query, entity_name, config
            ) VALUES (
                gen_random_uuid(), :ck, 'failed', 'Network timeout', 1, 0,
                now() - interval '10 minutes', now() - interval '9 minutes',
                'test query', 'test_retry_temple', '{}'::jsonb
            )
        """), {"ck": candidate_key})
        db.commit()

        # Mock classify_query so candidate_key matches
        mock_cls = {
            "entity_name": "test_retry_temple",
            "entity_name_ta": None,
            "place_name": "Pollachi",
            "district_id": 58,
            "is_heritage": True,
            "lang": "en",
        }

        with patch("app.services.augment_orchestrator.classify_query", return_value=mock_cls):
            with patch("app.services.augment_orchestrator.default_web_search", return_value=[]):
                res = run_augmentation_pipeline("test_retry_temple at Pollachi", db=db)
                # Must NOT return 'skipped_budget'
                assert res.status != "skipped_budget"
                # It executed and reached existence check / writeback gating
                assert res.status in {"rejected_nonexistent", "failed", "verified"}


def test_rule_2_1_reuse_verified_within_24h_no_web():
    """Rule 2.1: Reuses verified site within 24h with DecisionCode.VERIFIED_REUSED and 0 gemini calls."""
    candidate_key = "verified_temple_test|58"
    with SessionLocal() as db:
        db.execute(sa_text("DELETE FROM augment_jobs WHERE candidate_key = :ck"), {"ck": candidate_key})
        # Insert verified job from 2 hours ago
        db.execute(sa_text("""
            INSERT INTO augment_jobs (
                job_id, candidate_key, site_id, status, decision_reason, gemini_calls, gemini_tokens,
                started_at, finished_at, query, entity_name, config
            ) VALUES (
                gen_random_uuid(), :ck, 'TN-ERD-0001', 'verified', 'VERIFIED_NEW', 3, 0,
                now() - interval '2 hours', now() - interval '2 hours',
                'test query', 'verified_temple_test', '{}'::jsonb
            )
        """), {"ck": candidate_key})
        db.commit()

        mock_cls = {
            "entity_name": "verified_temple_test",
            "entity_name_ta": None,
            "place_name": "Pollachi",
            "district_id": 58,
            "is_heritage": True,
            "lang": "en",
        }

        # Mock search to fail if invoked
        def _no_web(*args, **kwargs):
            raise AssertionError("default_web_search was called despite 24h verified reuse!")

        with patch("app.services.augment_orchestrator.classify_query", return_value=mock_cls):
            with patch("app.services.augment_orchestrator.default_web_search", side_effect=_no_web):
                res = run_augmentation_pipeline("verified_temple_test at Pollachi", db=db)
                assert res.status == "verified"
                assert res.site_id == "TN-ERD-0001"
                assert res.decision_reason == DecisionCode.VERIFIED_REUSED.value
                assert res.gemini_calls == 0


def test_rule_2_1_unknown_candidate_keys_never_reused():
    """Rule 2.1: Candidate keys ending with '|unknown' are never reused."""
    candidate_key = "unknown_temple|unknown"
    with SessionLocal() as db:
        db.execute(sa_text("DELETE FROM augment_jobs WHERE candidate_key = :ck"), {"ck": candidate_key})
        db.execute(sa_text("""
            INSERT INTO augment_jobs (
                job_id, candidate_key, site_id, status, decision_reason, gemini_calls, gemini_tokens,
                started_at, finished_at, query, entity_name, config
            ) VALUES (
                gen_random_uuid(), :ck, 'TN-ERD-0001', 'verified', 'VERIFIED_NEW', 2, 0,
                now() - interval '1 hour', now(),
                'test query', 'unknown_temple', '{}'::jsonb
            )
        """), {"ck": candidate_key})
        db.commit()

        mock_cls = {
            "entity_name": "unknown_temple",
            "entity_name_ta": None,
            "place_name": None,
            "district_id": None,
            "is_heritage": True,
            "lang": "en",
        }

        with patch("app.services.augment_orchestrator.classify_query", return_value=mock_cls):
            with patch("app.services.augment_orchestrator.default_web_search", return_value=[]):
                res = run_augmentation_pipeline("unknown_temple", db=db)
                # Must not reuse the verified job
                assert res.decision_reason != DecisionCode.VERIFIED_REUSED.value


def test_rule_2_1_gemini_quota_exhausted_handling():
    """Rule 2.1: On HTTP 429 or quota limit, pipeline returns status quota_exhausted and polite message."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    mock_res = AugmentResult(
        job_id=uuid.uuid4(),
        candidate_key="quota_test|58",
        status="quota_exhausted",
        site_id=None,
        entity_name="quota_test",
        decision_reason=DecisionCode.QUOTA_EXHAUSTED.value,
        gemini_calls=0,
        latency_ms=50,
        sources_count=0,
    )

    with patch("app.services.chat_pipeline.run_augmentation_pipeline", return_value=mock_res):
        res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "rare temple at Pollachi"})
        assert res.status_code == 200
        data = res.json()
        assert "web checking is busy" in data["assistant_message"]["content"].lower()

        with SessionLocal() as db:
            user_mid = data["user_message"]["message_id"]
            log_row = db.execute(
                sa_text("SELECT path, path_detail FROM query_logs WHERE message_id = :mid"),
                {"mid": user_mid},
            ).mappings().fetchone()
            assert log_row is not None
            assert log_row["path_detail"]["status"] == "quota_exhausted"
            assert log_row["path_detail"]["decision_code"] == DecisionCode.QUOTA_EXHAUSTED.value
