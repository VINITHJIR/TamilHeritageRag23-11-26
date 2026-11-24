"""Phase 4 verification tests (Rules 4.1 - 4.4).

Verifies:
1. Provisional answer (Rule 4.1): existence not accepted but >= 1 quote-verified page ->
   status 'provisional', path 'web_provisional', writes 0 rows to sites/chunks, >= 1 to evidence_ledger.
2. Unconfirmed field formatting (Rule 4.1): existence accepted, unconfirmed fields shown as
   "Unconfirmed (1 source): <value> – <source>".
3. Conflict on asked field triggers needs_choice (Rule 4.2): 2-4 options + "Compare all",
   status 'needs_choice', path 'needs_choice', open clarification kind='value'.
4. User picks an option (Rule 4.2): detailed answer from that option's sources only,
   row inserted in option_choices, field_decisions and site_facts UNCHANGED.
5. User picks "Compare all" (Rule 4.2): side-by-side comparison of every option with sources.
6. Conflict on unasked field (Rule 4.2): does not trigger needs_choice; mentioned briefly in answer.
7. Unrelated question cancels pending value choice (Rule 4.3).
"""

import json
from unittest.mock import patch
import uuid
import pytest
from sqlalchemy import text as sa_text
from starlette.testclient import TestClient

from app.db.postgres import SessionLocal
from app.main import app
from app.services.augment_orchestrator import run_augmentation_pipeline, AugmentResult
from app.services.clarifications import (
    create_clarification,
    get_open_clarification,
)
from app.services.fact_consensus import FieldDecisionResult, evaluate_field_consensus
from app.services.spec_config import (
    DecisionCode,
    PROVISIONAL_ANSWER_LABEL,
    UNCONFIRMED_FIELD_PREFIX,
)

client = TestClient(app)


# -----------------------------------------------------------------------------
# 1. Provisional Answers (Rule 4.1)
# -----------------------------------------------------------------------------
def test_rule_4_1_provisional_writes_zero_sites_chunks_and_stored_in_evidence_ledger():
    """Rule 4.1: Existence not accepted but >= 1 relevant page has quote-verified claims.

    Must return status='provisional', path='web_provisional'.
    Must write 0 rows to sites and chunk_registry, and >= 1 row to evidence_ledger.
    """
    mock_entity = f"Test Provisional Shrine {uuid.uuid4().hex[:6]}"
    mock_query = f"{mock_entity} in Erode"

    mock_sources = [
        "https://travelblog-unknown.com/erode-shrine",
    ]

    mock_page_text = (
        f"{mock_entity} is an ancient shrine situated in Erode district. "
        "The presiding deity is Amman and it was built in 1985 CE."
    )

    mock_extracted_claims = [
        {
            "field": "deity",
            "raw_value": "Amman",
            "norm_value": "Amman",
            "evidence_quote": "The presiding deity is Amman",
            "quote_found": True,
        },
        {
            "field": "built_year",
            "raw_value": "1985",
            "norm_value": "1985",
            "evidence_quote": "built in 1985 CE",
            "quote_found": True,
        },
    ]

    with SessionLocal() as db:
        # Pre-counts
        sites_before = db.execute(sa_text("SELECT count(*) FROM sites WHERE name_en = :e"), {"e": mock_entity}).scalar()
        chunks_before = db.execute(sa_text("SELECT count(*) FROM chunk_registry")).scalar()
        ledger_before = db.execute(sa_text("SELECT count(*) FROM evidence_ledger")).scalar()

        with patch("app.services.augment_orchestrator.default_web_search", return_value=mock_sources), \
             patch("app.services.augment_orchestrator.fetch_page_content", return_value=(mock_page_text, 200, "https://travelblog-unknown.com/erode-shrine")), \
             patch("app.services.augment_orchestrator.extract_claims", return_value=mock_extracted_claims):

            res = run_augmentation_pipeline(
                query=mock_query,
                lang="en",
                db=db,
            )

            # Assert AugmentResult
            assert res.status == "provisional", f"Expected 'provisional' but got {res.status}"
            assert res.decision_reason == DecisionCode.PROVISIONAL_PARTIAL_EVIDENCE.value
            assert res.site_id is None, "Provisional must NOT allocate a site_id"
            assert len(res.provisional_claims) >= 1

            # Assert database state
            sites_after = db.execute(sa_text("SELECT count(*) FROM sites WHERE name_en = :e"), {"e": mock_entity}).scalar()
            chunks_after = db.execute(sa_text("SELECT count(*) FROM chunk_registry")).scalar()
            ledger_after = db.execute(sa_text("SELECT count(*) FROM evidence_ledger")).scalar()

            assert sites_after == sites_before, "Provisional path must write 0 rows to sites"
            assert chunks_after == chunks_before, "Provisional path must write 0 rows to chunk_registry"
            assert ledger_after > ledger_before, "Provisional path must write >= 1 row to evidence_ledger"


def test_rule_4_1_chat_pipeline_web_provisional_response_and_label():
    """Rule 4.1: Chat pipeline returns path='web_provisional' with provisional label and citations."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    mock_entity = f"Secret Cave Shrine {uuid.uuid4().hex[:6]}"
    mock_query = f"Tell me about {mock_entity} in Erode"

    mock_sources = ["https://random-traveler-blog.in/salem-cave"]
    mock_page = f"{mock_entity} in Erode district. The deity is Murugan."
    mock_claims = [
        {
            "field": "deity",
            "raw_value": "Murugan",
            "norm_value": "Murugan",
            "evidence_quote": "The deity is Murugan",
            "quote_found": True,
        }
    ]

    mock_classification = {
        "is_heritage": True,
        "lang": "en",
        "question_type": "general_heritage",
        "entity_name": mock_entity,
        "entity_name_ta": None,
        "place_name": "Erode",
        "district_id": 5,
        "subdistrict_id": None,
        "local_body_id": None,
        "entity_type": "specific_site",
        "asked_field": None,
        "primary_category": "temple",
        "fallback_used": False,
    }

    with patch("app.services.gemini.classify_query", return_value=mock_classification), \
         patch("app.services.chat_pipeline.gemini_synthesize_answer", return_value=f"{mock_entity} in Erode district."), \
         patch("app.services.augment_orchestrator.default_web_search", return_value=mock_sources), \
         patch("app.services.augment_orchestrator.fetch_page_content", return_value=(mock_page, 200, mock_sources[0])), \
         patch("app.services.augment_orchestrator.extract_claims", return_value=mock_claims):

        res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": mock_query})
        assert res.status_code == 200
        data = res.json()["assistant_message"]

        assert data["path"] == "web_provisional"
        assert PROVISIONAL_ANSWER_LABEL["en"] in data["content"]
        assert "Murugan" in data["content"]
        assert len(data["citations"]) >= 1


# -----------------------------------------------------------------------------
# 2. Unconfirmed Field Formatting when Existence Accepted (Rule 4.1)
# -----------------------------------------------------------------------------
def test_rule_4_1_existence_accepted_unconfirmed_field_formatting():
    """Rule 4.1: Verified fields answered normally; UNCONFIRMED fields shown as 'Unconfirmed (1 source): ...'."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    mock_entity = f"Thindal Velayuthaswamy {uuid.uuid4().hex[:6]}"
    mock_query = f"Tell me about {mock_entity} in Erode"

    # Tier-A verifies existence; Tier-B has builder with weight < 1.2 (unconfirmed)
    mock_sources = [
        "https://tnhrce.tn.gov.in/erode/thindal",
        "https://dinamalar.com/erode/thindal",
    ]
    mock_page_1 = f"{mock_entity} in Erode district. The deity is Murugan."
    mock_page_2 = f"{mock_entity} in Erode district. Built by King Cheran."

    def fake_fetch(url, *args, **kwargs):
        if "tnhrce.tn.gov.in" in url:
            return (mock_page_1, 200, url)
        return (mock_page_2, 200, url)

    def fake_extract(page_text, *args, **kwargs):
        if "Cheran" in page_text:
            return [{
                "field": "builder",
                "raw_value": "King Cheran",
                "norm_value": "King Cheran",
                "evidence_quote": "Built by King Cheran",
                "quote_found": True,
            }]
        return [{
            "field": "deity",
            "raw_value": "Lord Murugan",
            "norm_value": "Murugan",
            "evidence_quote": "The deity is Murugan",
            "quote_found": True,
        }]

    mock_classification = {
        "is_heritage": True,
        "lang": "en",
        "question_type": "general_heritage",
        "entity_name": mock_entity,
        "entity_name_ta": None,
        "place_name": "Erode",
        "district_id": 5,
        "subdistrict_id": None,
        "local_body_id": None,
        "entity_type": "specific_site",
        "asked_field": None,
        "primary_category": "temple",
        "fallback_used": False,
    }

    with patch("app.services.gemini.classify_query", return_value=mock_classification), \
         patch("app.services.chat_pipeline.gemini_synthesize_answer", return_value=f"{mock_entity} in Erode district."), \
         patch("app.services.augment_orchestrator.default_web_search", return_value=mock_sources), \
         patch("app.services.augment_orchestrator.fetch_page_content", side_effect=fake_fetch), \
         patch("app.services.augment_orchestrator.extract_claims", side_effect=fake_extract):

        res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": mock_query})
        assert res.status_code == 200
        data = res.json()["assistant_message"]

        assert data["path"] == "web_consensus"
        # Since single tier-B page has weight 0.60 < 1.2, builder is 'unconfirmed'
        assert UNCONFIRMED_FIELD_PREFIX["en"] in data["content"]
        assert "dinamalar.com" in data["content"]


# -----------------------------------------------------------------------------
# 3. Conflict on Asked Field Triggers needs_choice (Rule 4.2)
# -----------------------------------------------------------------------------
def test_rule_4_2_conflict_on_asked_field_returns_needs_choice():
    """Rule 4.2: Conflict on the asked field with 2-4 values -> status='needs_choice', path='needs_choice'."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    mock_entity = f"Conflict Temple {uuid.uuid4().hex[:6]}"
    mock_query = f"Who built {mock_entity} in Thanjavur?"

    # Two Tier-A sources with conflicting builders
    mock_sources = [
        "https://asi.nic.in/conflict-temple",
        "https://tnhrce.tn.gov.in/conflict-temple",
    ]
    mock_page_1 = f"{mock_entity} in Thanjavur district. Built by Raja Raja Chola I in 1010 CE."
    mock_page_2 = f"{mock_entity} in Thanjavur district. Built by Rajendra Chola I in 1025 CE."

    def fake_fetch(url, *args, **kwargs):
        if "asi.nic.in" in url:
            return (mock_page_1, 200, url)
        return (mock_page_2, 200, url)

    def fake_extract(page_text, *args, **kwargs):
        if "Raja Raja Chola I" in page_text:
            return [{
                "field": "builder",
                "raw_value": "Raja Raja Chola I",
                "norm_value": "Raja Raja Chola I",
                "evidence_quote": "Built by Raja Raja Chola I in 1010 CE",
                "quote_found": True,
            }]
        else:
            return [{
                "field": "builder",
                "raw_value": "Rajendra Chola I",
                "norm_value": "Rajendra Chola I",
                "evidence_quote": "Built by Rajendra Chola I in 1025 CE",
                "quote_found": True,
            }]

    mock_classification = {
        "is_heritage": True,
        "lang": "en",
        "question_type": "history",
        "entity_name": mock_entity,
        "entity_name_ta": None,
        "place_name": "Thanjavur",
        "district_id": 2,
        "subdistrict_id": None,
        "local_body_id": None,
        "entity_type": "specific_site",
        "asked_field": "builder",
        "primary_category": "temple",
        "fallback_used": False,
    }

    with patch("app.services.gemini.classify_query", return_value=mock_classification), \
         patch("app.services.augment_orchestrator.default_web_search", return_value=mock_sources), \
         patch("app.services.augment_orchestrator.fetch_page_content", side_effect=fake_fetch), \
         patch("app.services.augment_orchestrator.extract_claims", side_effect=fake_extract):

        res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": mock_query})
        assert res.status_code == 200
        data = res.json()["assistant_message"]

        assert data["path"] == "needs_choice"
        assert "Sources give different answers for builder:" in data["content"]
        assert "Raja Raja Chola I" in data["content"]
        assert "Rajendra Chola I" in data["content"]
        assert "Compare all" in data["content"]
        assert "Which source's details would you like to see?" in data["content"]

        # Check open clarification record created
        with SessionLocal() as db:
            open_clar = get_open_clarification(db, session_id)
            assert open_clar is not None
            assert open_clar.kind == "value"
            assert open_clar.field == "builder"
            assert len(open_clar.candidates) >= 3  # 2 values + "Compare all"


# -----------------------------------------------------------------------------
# 4. Picking an Option Resolves Value Choice (Rule 4.2)
# -----------------------------------------------------------------------------
def test_rule_4_2_picking_an_option_resolves_choice_and_records_in_option_choices():
    """Rule 4.2: User picks an option -> detailed answer citing only that option's sources.

    Must insert row in option_choices table.
    Must leave field_decisions and site_facts completely UNCHANGED.
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    mock_entity = "Brihadeeswarar Temple"
    candidates = [
        {
            "choice_id": "1",
            "value": "Raja Raja Chola I",
            "sources": [{"domain": "asi.nic.in", "tier": "A", "url": "https://asi.nic.in/tanjore"}],
            "support_ratio": 0.70,
            "has_tier_a": True,
        },
        {
            "choice_id": "2",
            "value": "Rajendra Chola I",
            "sources": [{"domain": "tamilnadu-tourism.org", "tier": "B", "url": "https://tamilnadu-tourism.org/tanjore"}],
            "support_ratio": 0.30,
            "has_tier_a": False,
        },
        {
            "choice_id": "3",
            "value": "Compare all",
            "sources": [],
            "support_ratio": 0.0,
            "has_tier_a": False,
        },
    ]

    with SessionLocal() as db:
        # Create open value clarification
        clar = create_clarification(
            db=db,
            session_id=session_id,
            asked_message_id=1,
            entity_name=mock_entity,
            entity_name_ta=None,
            question_type="history",
            original_query="Who built Brihadeeswarar temple?",
            candidates=candidates,
            kind="value",
            field="builder",
        )

        # Baseline checks on field_decisions and site_facts
        fd_count_before = db.execute(sa_text("SELECT count(*) FROM field_decisions")).scalar()
        sf_count_before = db.execute(sa_text("SELECT count(*) FROM site_facts")).scalar()
        choices_before = db.execute(sa_text("SELECT count(*) FROM option_choices")).scalar()

    # User picks option 1 by sending "1"
    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "1"})
    assert res.status_code == 200
    data = res.json()["assistant_message"]

    assert data["path"] == "web_consensus"
    assert "According to asi.nic.in (Tier A)" in data["content"]
    assert "Raja Raja Chola I" in data["content"]
    assert "Rajendra Chola I" in data["content"]  # mentions alternative briefly
    assert len(data["citations"]) == 1
    assert data["citations"][0]["origin"] == "asi.nic.in"

    with SessionLocal() as db:
        # Check clarification resolved
        clar_after = get_open_clarification(db, session_id)
        assert clar_after is None, "Clarification must be marked resolved"

        # Check option_choices table got exactly 1 new row
        choices_after = db.execute(sa_text("SELECT count(*) FROM option_choices")).scalar()
        assert choices_after == choices_before + 1

        choice_row = db.execute(
            sa_text("SELECT field, chosen_value, options FROM option_choices ORDER BY created_at DESC LIMIT 1")
        ).mappings().fetchone()
        assert choice_row["field"] == "builder"
        assert choice_row["chosen_value"] == "Raja Raja Chola I"
        assert len(choice_row["options"]) == 3

        # CRITICAL RULE 4.2 ASSERTION: field_decisions and site_facts MUST REMAIN UNCHANGED
        fd_count_after = db.execute(sa_text("SELECT count(*) FROM field_decisions")).scalar()
        sf_count_after = db.execute(sa_text("SELECT count(*) FROM site_facts")).scalar()
        assert fd_count_after == fd_count_before, "User pick MUST NEVER alter field_decisions"
        assert sf_count_after == sf_count_before, "User pick MUST NEVER alter site_facts"


# -----------------------------------------------------------------------------
# 5. User Picks "Compare all" (Rule 4.2)
# -----------------------------------------------------------------------------
def test_rule_4_2_picking_compare_all_lists_all_options_side_by_side():
    """Rule 4.2: User picks 'Compare all' -> produces side-by-side comparison of every option."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    mock_entity = "Gangaikonda Cholapuram"
    candidates = [
        {
            "choice_id": "1",
            "value": "1035 CE",
            "sources": [{"domain": "asi.nic.in", "tier": "A", "url": "https://asi.nic.in/gkc"}],
            "support_ratio": 0.65,
            "has_tier_a": True,
        },
        {
            "choice_id": "2",
            "value": "1025 CE",
            "sources": [{"domain": "tn-archaeology.gov.in", "tier": "A", "url": "https://tn-archaeology.gov.in/gkc"}],
            "support_ratio": 0.35,
            "has_tier_a": True,
        },
        {
            "choice_id": "3",
            "value": "Compare all",
            "sources": [],
            "support_ratio": 0.0,
            "has_tier_a": False,
        },
    ]

    with SessionLocal() as db:
        create_clarification(
            db=db,
            session_id=session_id,
            asked_message_id=1,
            entity_name=mock_entity,
            entity_name_ta=None,
            question_type="history",
            original_query="When was Gangaikonda Cholapuram built?",
            candidates=candidates,
            kind="value",
            field="built_year",
        )

    # User replies "Compare all"
    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "Compare all"})
    assert res.status_code == 200
    data = res.json()["assistant_message"]

    assert data["path"] == "web_consensus"
    assert "Side-by-side comparison of sources" in data["content"]
    assert "1035 CE" in data["content"]
    assert "65%" in data["content"]
    assert "1025 CE" in data["content"]
    assert "35%" in data["content"]
    assert len(data["citations"]) >= 2


# -----------------------------------------------------------------------------
# 6. Conflict on Unasked Field Mentioned Briefly (Rule 4.2)
# -----------------------------------------------------------------------------
def test_rule_4_2_conflict_on_unasked_field_does_not_trigger_needs_choice():
    """Rule 4.2: If conflict is on a field the user did NOT ask about, do not ask; mention it briefly."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    mock_entity = f"Overview Temple {uuid.uuid4().hex[:6]}"
    # User asks for overview (asked_field = "overview")
    mock_query = f"Tell me about {mock_entity} in Madurai"

    mock_sources = [
        "https://tnhrce.tn.gov.in/madurai/overview-temple",
        "https://asi.nic.in/madurai/overview-temple",
    ]
    mock_page_1 = f"{mock_entity} in Madurai district. Built by Kadava King. Overview is great temple."
    mock_page_2 = f"{mock_entity} in Madurai district. Built by Pandya King. Overview is great temple."

    def fake_extract(page_text, *args, **kwargs):
        if "Kadava King" in page_text:
            return [{
                "field": "builder",
                "raw_value": "Kadava King",
                "norm_value": "Kadava King",
                "evidence_quote": "Built by Kadava King",
                "quote_found": True,
            }]
        else:
            return [{
                "field": "builder",
                "raw_value": "Pandya King",
                "norm_value": "Pandya King",
                "evidence_quote": "Built by Pandya King",
                "quote_found": True,
            }]

    mock_classification = {
        "is_heritage": True,
        "lang": "en",
        "question_type": "general_heritage",
        "entity_name": mock_entity,
        "entity_name_ta": None,
        "place_name": "Madurai",
        "district_id": 4,
        "subdistrict_id": None,
        "local_body_id": None,
        "entity_type": "specific_site",
        "asked_field": "overview",
        "primary_category": "temple",
        "fallback_used": False,
    }

    with patch("app.services.gemini.classify_query", return_value=mock_classification), \
         patch("app.services.augment_orchestrator.default_web_search", return_value=mock_sources), \
         patch("app.services.augment_orchestrator.fetch_page_content", return_value=(mock_page_1, 200, mock_sources[0])), \
         patch("app.services.augment_orchestrator.extract_claims", side_effect=fake_extract):

        res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": mock_query})
        assert res.status_code == 200
        data = res.json()["assistant_message"]

        # Must NOT be needs_choice because user asked for overview
        assert data["path"] == "web_consensus"
        # Must mention conflicting builder briefly in note
        assert "conflicting accounts for builder" in data["content"].lower() or "kadava" in data["content"].lower()


# -----------------------------------------------------------------------------
# 7. Unrelated Question Cancels Pending Value Choice (Rule 4.3)
# -----------------------------------------------------------------------------
def test_rule_4_3_unrelated_question_cancels_pending_choice():
    """Rule 4.3: An unrelated question cancels the open choice and proceeds normally."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    candidates = [
        {"choice_id": "1", "value": "Option A", "sources": [], "support_ratio": 0.5},
        {"choice_id": "2", "value": "Option B", "sources": [], "support_ratio": 0.5},
        {"choice_id": "3", "value": "Compare all", "sources": [], "support_ratio": 0.0},
    ]

    with SessionLocal() as db:
        create_clarification(
            db=db,
            session_id=session_id,
            asked_message_id=1,
            entity_name="Some Temple",
            entity_name_ta=None,
            question_type="history",
            original_query="Some question",
            candidates=candidates,
            kind="value",
            field="builder",
        )

    # User asks unrelated greeting
    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "hi"})
    assert res.status_code == 200
    data = res.json()["assistant_message"]

    assert data["path"] == "greeting"

    with SessionLocal() as db:
        open_clar = get_open_clarification(db, session_id)
        assert open_clar is None, "Pending choice must be cancelled"
