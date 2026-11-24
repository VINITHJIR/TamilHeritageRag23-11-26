"""Phase 1 verification tests (Rules 1.1 - 1.5).

Tests:
1. "what is ur name" -> out_of_scope, no augment job (Rule 1.2).
2. "hi" -> greeting fast-path (Rule 1.1).
3. question_type never empty across various queries (Rule 1.1).
4. "masani amman temple at pollachi" -> resolves to district Coimbatore (Rule 1.3).
5. Message traces: message_traces table records pipeline stages and GET /api/admin/messages/{id}/trace (Rule 1.4).
6. Web check refusal never says "in our database" (Rule 1.5).
"""

from unittest.mock import patch
import pytest
from starlette.testclient import TestClient
from sqlalchemy import text as sa_text
from app.main import app
from app.db.postgres import SessionLocal
from app.services.gemini import classify_query
from app.services.place_resolution import resolve_place
from app.services.chat_pipeline import _build_web_checked_rejection

client = TestClient(app)


def test_rule_1_1_greeting_fast_path():
    """Rule 1.1: 'hi' hits greeting fast-path without classification or augmentation."""
    guest_res = client.post("/api/users/guest")
    assert guest_res.status_code == 200
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "hi"})
    assert res.status_code == 200
    data = res.json()
    assert data["assistant_message"]["path"] == "greeting"
    assert data["assistant_message"]["question_type"] == "general_heritage"
    assert "Hello" in data["assistant_message"]["content"]


def test_rule_1_1_question_type_never_empty():
    """Rule 1.1: classify_query must never return an empty question_type."""
    queries = [
        "hi",
        "what is ur name",
        "madurai meenakshi",
        "pariyur amman temple pathi sollu",
        "random test 123456",
        "",
        "???",
    ]
    with SessionLocal() as db:
        for q in queries:
            res = classify_query(q, db=db)
            assert res["question_type"], f"question_type was empty for query: {q}"
            assert isinstance(res["question_type"], str)
            assert len(res["question_type"]) > 0


def test_rule_1_2_out_of_scope_no_augment_job():
    """Rule 1.2: 'what is ur name' -> out_of_scope, polite reply, NO augmentation job."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    with SessionLocal() as db:
        job_count_before = db.execute(sa_text("SELECT COUNT(*) FROM augment_jobs")).scalar()

    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "what is ur name"})
    assert res.status_code == 200
    data = res.json()
    asst_msg = data["assistant_message"]

    assert asst_msg["path"] == "out_of_scope"
    assert "heritage" in asst_msg["content"].lower() or "tamil nadu" in asst_msg["content"].lower()

    with SessionLocal() as db:
        job_count_after = db.execute(sa_text("SELECT COUNT(*) FROM augment_jobs")).scalar()
        # Ensure NO augmentation job was created
        assert job_count_after == job_count_before

        # Ensure query_logs recorded out_of_scope path and DecisionCode.OUT_OF_SCOPE
        user_mid = data["user_message"]["message_id"]
        log_row = db.execute(
            sa_text("SELECT path, path_detail FROM query_logs WHERE message_id = :mid"),
            {"mid": user_mid},
        ).mappings().fetchone()
        assert log_row is not None
        assert log_row["path"] == "out_of_scope"
        assert log_row["path_detail"]["status"] == "out_of_scope"
        assert log_row["path_detail"]["decision_code"] == "OUT_OF_SCOPE"


def test_rule_1_3_place_resolution_pollachi_to_coimbatore():
    """Rule 1.3: 'masani amman temple at pollachi' resolves to Coimbatore district."""
    with SessionLocal() as db:
        # Test place_resolution service directly
        resolved = resolve_place("masani amman temple at pollachi", place_name=None, db=db)
        assert resolved.district_name == "Coimbatore"
        assert resolved.district_id is not None

        # Test classify_query integration
        cls = classify_query("masani amman temple at pollachi", db=db)
        assert cls["is_heritage"] is True
        assert cls["district_id"] == resolved.district_id


def test_rule_1_4_message_traces_and_admin_endpoint():
    """Rule 1.4: Pipeline stages are recorded into message_traces and exposed via GET /api/admin/messages/{id}/trace."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    sess_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = sess_res.json()["session_id"]

    msg_res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "Tell me about Brihadeeswarar Temple in Thanjavur"
    })
    assert msg_res.status_code == 200
    user_msg_id = msg_res.json()["user_message"]["message_id"]

    # Call admin trace endpoint
    trace_res = client.get(f"/api/admin/messages/{user_msg_id}/trace")
    assert trace_res.status_code == 200, trace_res.text
    trace_data = trace_res.json()
    assert trace_data["message_id"] == user_msg_id
    assert len(trace_data["stages"]) > 0

    stage_names = [s["stage"] for s in trace_data["stages"]]
    assert "classification" in stage_names
    assert "place_resolution" in stage_names
    assert "site_detection" in stage_names
    assert "vector_search" in stage_names


def test_rule_1_5_web_checked_rejection_never_says_in_our_database():
    """Rule 1.5: When a web check runs, refusal explicitly states what was checked, never 'in our database'."""
    rejection = _build_web_checked_rejection(
        lang="en",
        n_sources=7,
        entity_label="Sri Kaviyan Temple",
        district_label=" in Erode district",
    )
    assert "in our database" not in rejection.lower()
    assert "checked 7 web sources" in rejection
    assert "could not confirm Sri Kaviyan Temple in Erode district" in rejection

    # Test Tamil version
    rejection_ta = _build_web_checked_rejection(
        lang="ta",
        n_sources=7,
        entity_label="கவியன் கோவில்",
        district_label=" ஈரோடு மாவட்டம்",
    )
    assert "7 இணையத் தளங்களை சரிபார்த்தேன்" in rejection_ta
