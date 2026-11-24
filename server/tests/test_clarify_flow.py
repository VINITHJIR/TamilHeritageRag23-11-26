# Integration tests for the clarify-then-augment flow
"""
Tests for the CLARIFY-THEN-AUGMENT flow.

Run with: pytest server/tests/test_clarify_flow.py -v
Requires a live PostgreSQL test DB and the 004_clarifications migration applied.
"""

import uuid
import pytest
from unittest.mock import MagicMock, patch, ANY

from app.services.clarifications import (
    create_clarification,
    get_open_clarification,
    resolve_clarification,
    cancel_clarification,
)
from app.augment.clarify import process_clarification_reply, _similarity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_id() -> str:
    return str(uuid.uuid4())


def _mock_clarification(session_id: str, candidates=None):
    """Return a mock Clarification object for unit tests."""
    clar = MagicMock()
    clar.clarification_id = uuid.uuid4()
    clar.session_id = session_id
    clar.entity_name = "Ponniamman Temple"
    clar.question_type = "general_heritage"
    clar.original_query = "are you know about ponniamman temple"
    clar.candidates = candidates or [
        {"option_id": "opt-1", "locality": "Egmore", "district": "Chennai", "label": "Egmore Chennai"},
        {"option_id": "opt-2", "locality": "Sholinganallur", "district": "Chennai", "label": "Sholinganallur Chennai"},
    ]
    clar.status = "open"
    from datetime import datetime
    clar.created_at = datetime.utcnow()
    return clar


# ---------------------------------------------------------------------------
# Unit tests: _similarity helper
# ---------------------------------------------------------------------------

class TestSimilarityHelper:
    def test_exact_match(self):
        assert _similarity("Egmore", "Egmore") == 100

    def test_partial_match(self):
        score = _similarity("Egmore Chennai", "egmore")
        assert score >= 50  # partial match should be moderate

    def test_no_match(self):
        score = _similarity("Madurai", "Chennai")
        assert score < 50

    def test_case_insensitive(self):
        assert _similarity("EGMORE", "egmore") == 100


# ---------------------------------------------------------------------------
# Unit tests: process_clarification_reply
# ---------------------------------------------------------------------------

class TestProcessClarificationReply:
    def _db_with_clar(self, clar):
        """Return a mock DB where get_open_clarification returns `clar`."""
        db = MagicMock()
        return db

    def test_no_open_clarification_returns_unresolved(self):
        db = MagicMock()
        with patch("app.augment.clarify.get_open_clarification", return_value=None):
            result = process_clarification_reply(db, _make_session_id(), {"text": "Egmore"})
        assert result["status"] == "unresolved"

    def test_chip_click_valid_option(self):
        session_id = _make_session_id()
        clar = _mock_clarification(session_id)
        with patch("app.augment.clarify.get_open_clarification", return_value=clar), \
             patch("app.augment.clarify.resolve_clarification") as mock_resolve:
            mock_resolve.return_value = clar
            result = process_clarification_reply(db=MagicMock(), session_id=session_id, reply_payload={"option_id": "opt-1"})
        assert result["status"] == "resolved"
        assert result["selected_candidate"]["option_id"] == "opt-1"

    def test_chip_click_invalid_option_cancels(self):
        session_id = _make_session_id()
        clar = _mock_clarification(session_id)
        with patch("app.augment.clarify.get_open_clarification", return_value=clar), \
             patch("app.augment.clarify.cancel_clarification") as mock_cancel:
            mock_cancel.return_value = clar
            result = process_clarification_reply(db=MagicMock(), session_id=session_id, reply_payload={"option_id": "bad-id"})
        assert result["status"] == "cancelled"

    def test_free_text_exact_locality_resolves(self):
        session_id = _make_session_id()
        clar = _mock_clarification(session_id)
        with patch("app.augment.clarify.get_open_clarification", return_value=clar), \
             patch("app.augment.clarify.resolve_clarification") as mock_resolve:
            mock_resolve.return_value = clar
            result = process_clarification_reply(db=MagicMock(), session_id=session_id, reply_payload={"text": "Egmore Chennai"})
        assert result["status"] == "resolved"

    def test_free_text_no_match_cancels(self):
        session_id = _make_session_id()
        clar = _mock_clarification(session_id)
        with patch("app.augment.clarify.get_open_clarification", return_value=clar), \
             patch("app.augment.clarify.cancel_clarification") as mock_cancel:
            mock_cancel.return_value = clar
            result = process_clarification_reply(db=MagicMock(), session_id=session_id, reply_payload={"text": "Tambaram"})
        assert result["status"] == "cancelled"

    def test_empty_reply_unresolved(self):
        session_id = _make_session_id()
        clar = _mock_clarification(session_id)
        with patch("app.augment.clarify.get_open_clarification", return_value=clar):
            result = process_clarification_reply(db=MagicMock(), session_id=session_id, reply_payload={"text": ""})
        assert result["status"] == "unresolved"


# ---------------------------------------------------------------------------
# Unit tests: disambiguate_query return statuses
# ---------------------------------------------------------------------------

class TestDisambiguateQuery:
    @pytest.mark.parametrize("candidates,expected_status", [
        ([], "rejected_nonexistent"),
        ([{"option_id": "o1", "locality": "Egmore", "district": "Chennai"}], "resolved_direct"),
        (
            [
                {"option_id": "o1", "locality": "Egmore", "district": "Chennai"},
                {"option_id": "o2", "locality": "Ambattur", "district": "Chennai"},
            ],
            "needs_clarification",
        ),
    ])
    def test_disambiguation_status(self, candidates, expected_status):
        from app.augment.disambiguate import disambiguate_query
        db = MagicMock()

        with patch("app.augment.disambiguate.get_open_clarification", return_value=None), \
             patch("app.augment.disambiguate._load_cached_candidates", return_value=candidates), \
             patch("app.augment.disambiguate.create_clarification", return_value=MagicMock(clarification_id=uuid.uuid4())):
            result = disambiguate_query(
                db=db,
                session_id=str(uuid.uuid4()),
                asked_message_id="1",
                entity_name="Ponniamman Temple",
                entity_name_ta=None,
                question_type="general_heritage",
                original_query="ponniamman temple",
            )
        assert result["status"] == expected_status
