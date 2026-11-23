"""Pytest test suite for Chat API (Level 1B Phase 1).

Covers:
- POST /api/users/guest: guest user creation.
- GET/POST/PATCH /api/chat/sessions: session creation, listing, updating.
- GET /api/chat/sessions/{id}/messages: session message retrieval with citations.
- POST /api/chat/sessions/{id}/messages:
  - Tamil query: language detection ('ta'), question classification ('history'),
    site detection ('TN-TNJ-0001'), vector retrieval, template prefix, citations.
  - English query: language detection ('en'), 'db' path.
  - Tanglish query: language detection ('ta-Latn'), question classification ('festival'),
    site detection ('TN-ERD-0001').
  - Kannada query: language detection ('kn'), question classification ('location'),
    site detection ('TN-ERD-0001').
  - Rejection path: top_score < 0.45 or out-of-scope -> path='rejected', citations=[], polite message.
- POST /api/messages/{id}/feedback: rating, reason, comment, and upsert idempotency.
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sa_text
from app.db.postgres import SessionLocal
from app.main import app

client = TestClient(app)


def test_create_guest_user():
    """POST /api/users/guest -> creates user with auth_provider='guest', returns user_id."""
    response = client.post("/api/users/guest")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "user_id" in data
    assert data["auth_provider"] == "guest"

    # Verify user exists in PostgreSQL
    with SessionLocal() as db:
        user_row = db.execute(
            sa_text("SELECT auth_provider, role FROM users WHERE user_id = :uid"),
            {"uid": data["user_id"]},
        ).mappings().fetchone()
        assert user_row is not None
        assert user_row["auth_provider"] == "guest"
        assert user_row["role"] == "user"


def test_session_crud():
    """Test session creation, listing, updating, and message retrieval."""
    # 1. Create a guest user
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]

    # 2. Create session
    create_res = client.post("/api/chat/sessions", json={
        "user_id": user_id,
        "title": "Initial Chat",
        "client": "web",
    })
    assert create_res.status_code == 201, create_res.text
    session_data = create_res.json()
    session_id = session_data["session_id"]
    assert session_data["title"] == "Initial Chat"
    assert session_data["client"] == "web"
    assert session_data["is_archived"] is False

    # 3. List sessions for user
    list_res = client.get(f"/api/chat/sessions?user_id={user_id}")
    assert list_res.status_code == 200
    sessions = list_res.json()
    assert any(s["session_id"] == session_id for s in sessions)

    # 4. Update session title and archive status
    patch_res = client.patch(f"/api/chat/sessions/{session_id}", json={
        "title": "Renamed Heritage Chat",
        "is_archived": True,
    })
    assert patch_res.status_code == 200
    updated_data = patch_res.json()
    assert updated_data["title"] == "Renamed Heritage Chat"
    assert updated_data["is_archived"] is True

    # 5. Get messages (currently empty)
    msgs_res = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert msgs_res.status_code == 200
    assert msgs_res.json() == []


def test_chat_pipeline_tamil():
    """Tamil query: 'தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?'

    Validates:
    - lang = 'ta'
    - question_type = 'history'
    - resolved_site = 'TN-TNJ-0001'
    - path = 'db'
    - extractive answer with Tamil prefix
    - citations with site_id='TN-TNJ-0001'
    - first message auto-updates session title
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]

    session_res = client.post("/api/chat/sessions", json={"user_id": user_id})
    session_id = session_res.json()["session_id"]

    msg_res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?"
    })
    assert msg_res.status_code == 200, msg_res.text
    data = msg_res.json()

    user_msg = data["user_message"]
    assert user_msg["role"] == "user"
    assert user_msg["lang"] == "ta"
    assert user_msg["question_type"] == "history"
    assert user_msg["resolved_site"] == "TN-TNJ-0001"

    asst_msg = data["assistant_message"]
    assert asst_msg["role"] == "assistant"
    assert asst_msg["lang"] == "ta"
    assert asst_msg["path"] == "db"
    assert asst_msg["top_score"] >= 0.45
    assert "எங்கள் பதிவுகளின்படி:" in asst_msg["content"]
    assert len(asst_msg["citations"]) > 0
    assert asst_msg["citations"][0]["site_id"] == "TN-TNJ-0001"
    assert asst_msg["reply_to"] == user_msg["message_id"]

    # Verify session title was updated
    session_check = client.get(f"/api/chat/sessions?user_id={user_id}").json()
    assert session_check[0]["title"] == "தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?"


def test_chat_pipeline_english():
    """English query: 'Who built the Big Temple in Thanjavur?'

    Validates:
    - lang = 'en'
    - question_type = 'history'
    - resolved_site = 'TN-TNJ-0001'
    - path = 'db'
    - extractive answer with English prefix
    - citations present
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    msg_res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "Who built the Big Temple in Thanjavur?"
    })
    assert msg_res.status_code == 200, msg_res.text
    data = msg_res.json()

    user_msg = data["user_message"]
    assert user_msg["lang"] == "en"
    assert user_msg["question_type"] == "history"
    assert user_msg["resolved_site"] == "TN-TNJ-0001"

    asst_msg = data["assistant_message"]
    assert asst_msg["lang"] == "en"
    assert asst_msg["path"] == "db"
    assert asst_msg["top_score"] >= 0.45
    assert "From our heritage records:" in asst_msg["content"]
    assert len(asst_msg["citations"]) > 0
    assert asst_msg["citations"][0]["site_id"] == "TN-TNJ-0001"


def test_chat_pipeline_tanglish():
    """Tanglish query: 'bannari amman kovil la enna festival nadakkum'

    Validates:
    - lang = 'ta-Latn'
    - question_type = 'festival'
    - resolved_site = 'TN-ERD-0001'
    - path = 'db'
    - citations point to TN-ERD-0001
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    msg_res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "bannari amman kovil la enna festival nadakkum"
    })
    assert msg_res.status_code == 200, msg_res.text
    data = msg_res.json()

    user_msg = data["user_message"]
    assert user_msg["lang"] == "ta-Latn"
    assert user_msg["question_type"] == "festival"
    assert user_msg["resolved_site"] == "TN-ERD-0001"

    asst_msg = data["assistant_message"]
    assert asst_msg["path"] == "db"
    assert asst_msg["top_score"] >= 0.45
    assert len(asst_msg["citations"]) > 0
    assert asst_msg["citations"][0]["site_id"] == "TN-ERD-0001"


def test_chat_pipeline_kannada():
    """Kannada query: 'ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?'

    Validates:
    - lang = 'kn'
    - question_type = 'location'
    - resolved_site = 'TN-ERD-0001'
    - path = 'db'
    - extractive answer with Kannada prefix
    - citations point to TN-ERD-0001
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    msg_res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?"
    })
    assert msg_res.status_code == 200, msg_res.text
    data = msg_res.json()

    user_msg = data["user_message"]
    assert user_msg["lang"] == "kn"
    assert user_msg["question_type"] == "location"
    assert user_msg["resolved_site"] == "TN-ERD-0001"

    asst_msg = data["assistant_message"]
    assert asst_msg["lang"] == "kn"
    assert asst_msg["path"] == "db"
    assert asst_msg["top_score"] >= 0.45
    assert "ನಮ್ಮ ದಾಖಲೆಗಳ ಪ್ರಕಾರ:" in asst_msg["content"]
    assert len(asst_msg["citations"]) > 0
    assert asst_msg["citations"][0]["site_id"] == "TN-ERD-0001"


def test_chat_pipeline_rejection_path():
    """Rejection path:
    1. Unknown / low-relevance heritage query -> score < 0.45 -> path='rejected', citations=[]
    2. Completely out-of-scope query (e.g. weather/crypto) -> path='rejected', citations=[]
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    # 1. Non-existent temple
    msg_res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "Tell me about Sri Kaviyan Temple in Erode"
    })
    assert msg_res.status_code == 200, msg_res.text
    data = msg_res.json()
    asst_msg = data["assistant_message"]
    assert asst_msg["path"] == "rejected"
    assert asst_msg["citations"] == []
    assert "could not find reliable records" in asst_msg["content"]

    # 2. Out of scope in Tamil
    msg_res_ta = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "இன்று சென்னையில் வானிலை (weather) எப்படி இருக்கும்?"
    })
    assert msg_res_ta.status_code == 200, msg_res_ta.text
    data_ta = msg_res_ta.json()
    asst_ta = data_ta["assistant_message"]
    assert asst_ta["path"] == "rejected"
    assert asst_ta["citations"] == []
    assert "மன்னிக்கவும்" in asst_ta["content"]


def test_message_feedback_upsert():
    """POST /api/messages/{id}/feedback -> rating (+1 / -1), reasons, and upsert idempotency."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    msg_res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "Who built the Big Temple in Thanjavur?"
    })
    assistant_msg_id = msg_res.json()["assistant_message"]["message_id"]

    # 1. Submit positive feedback
    fb1 = client.post(f"/api/messages/{assistant_msg_id}/feedback", json={
        "user_id": user_id,
        "rating": 1,
        "comment": "Accurate historical answer",
    })
    assert fb1.status_code == 200, fb1.text
    body1 = fb1.json()
    assert body1["status"] == "ok"
    assert body1["rating"] == 1
    assert body1["comment"] == "Accurate historical answer"

    # 2. Upsert with negative feedback and reason
    fb2 = client.post(f"/api/messages/{assistant_msg_id}/feedback", json={
        "user_id": user_id,
        "rating": -1,
        "reason": "incomplete",
        "comment": "Could provide more architectural details",
    })
    assert fb2.status_code == 200, fb2.text
    body2 = fb2.json()
    assert body2["status"] == "ok"
    assert body2["rating"] == -1
    assert body2["reason"] == "incomplete"
    assert body2["comment"] == "Could provide more architectural details"

    # Verify only ONE row exists in message_feedback for this (message_id, user_id)
    with SessionLocal() as db:
        count = db.execute(
            sa_text("SELECT count(*) FROM message_feedback WHERE message_id = :mid AND user_id = :uid"),
            {"mid": assistant_msg_id, "uid": user_id},
        ).scalar()
        assert count == 1


def test_get_session_messages_history():
    """GET /api/chat/sessions/{id}/messages verifies entire conversation history with citations."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    # Post message
    client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "Where is Bannari Amman Temple?"
    })

    history_res = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert history_res.status_code == 200
    msgs = history_res.json()
    assert len(msgs) == 2  # user + assistant

    user_m = msgs[0]
    assert user_m["role"] == "user"
    assert user_m["content"] == "Where is Bannari Amman Temple?"

    asst_m = msgs[1]
    assert asst_m["role"] == "assistant"
    assert asst_m["reply_to"] == user_m["message_id"]
    assert asst_m["path"] == "db"
    assert len(asst_m["citations"]) > 0
    assert asst_m["citations"][0]["site_id"] == "TN-ERD-0001"


# -----------------------------------------------------------------------------
# Regression Tests for Phase 0 Bug Fixes
# -----------------------------------------------------------------------------
def test_identity_check_pariyur_rejected():
    """Identity bug regression test:

    'pariyur amman temple pathi sollu' mentions Pariyur Amman Temple (not in DB).
    Must NOT drift to Bannari Amman Temple (TN-ERD-0001).
    Must return path='rejected' with citations=[].
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "pariyur amman temple pathi sollu"
    })
    assert res.status_code == 200, res.text
    data = res.json()
    asst = data["assistant_message"]

    assert asst["path"] == "rejected"
    assert asst["citations"] == []
    # Assert it did NOT resolve to Bannari Amman Temple
    assert asst["resolved_site"] is None or asst["resolved_site"] != "TN-ERD-0001"
    for cit in asst["citations"]:
        assert cit["site_id"] != "TN-ERD-0001"


def test_identity_check_bannari_accepted():
    """Identity check regression test:

    'bannari amman kovil la enna festival nadakkum' mentions Bannari Amman Temple.
    Must accurately match TN-ERD-0001 with path='db' and include citations for TN-ERD-0001.
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "bannari amman kovil la enna festival nadakkum"
    })
    assert res.status_code == 200, res.text
    data = res.json()
    asst = data["assistant_message"]

    assert asst["path"] == "db"
    assert len(asst["citations"]) > 0
    assert asst["citations"][0]["site_id"] == "TN-ERD-0001"
    assert "bannari" in asst["content"].lower() or "பண்ணாரி" in asst["content"]


def test_tanglish_detection_extended():
    """Verify extended Tanglish marker detection for pathi, sollu, enga, irukku, eppo, yaar, etc."""
    from app.services.chat_pipeline import detect_language

    # Markers tested
    assert detect_language("pariyur amman temple pathi sollu") == "ta-Latn"
    assert detect_language("bannari amman kovil enga irukku") == "ta-Latn"
    assert detect_language("kovil festival eppo nadakkum") == "ta-Latn"
    assert detect_language("yaar kattinaanga indha temple") == "ta-Latn"
    assert detect_language("thanjavur-la enna irukku") == "ta-Latn"
    assert detect_language("kovil-ku eppadi ponum") == "ta-Latn"
    assert detect_language("kovil-oda varalaaru enna") == "ta-Latn"
    assert detect_language("bannari amman kovil la enna festival nadakkum") == "ta-Latn"


def test_message_idempotency():
    """Verify duplicate assistant messages are prevented using client idempotency_key."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    idempotency_key = f"test-idem-{uuid.uuid4()}"

    # First request
    res1 = client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"content": "Tell me about Thanjavur temple", "idempotency_key": idempotency_key},
    )
    assert res1.status_code == 200, res1.text
    data1 = res1.json()
    asst_id_1 = data1["assistant_message"]["message_id"]

    # Second request with the same idempotency_key (e.g. rapid double click / network retry)
    res2 = client.post(
        f"/api/chat/sessions/{session_id}/messages",
        headers={"Idempotency-Key": idempotency_key},
        json={"content": "Tell me about Thanjavur temple"},
    )
    assert res2.status_code == 200, res2.text
    data2 = res2.json()
    asst_id_2 = data2["assistant_message"]["message_id"]

    # Responses must be identical
    assert asst_id_1 == asst_id_2
    assert data1["assistant_message"]["content"] == data2["assistant_message"]["content"]

    # Verify database has exactly 1 assistant message for this reply
    with SessionLocal() as db:
        user_msgs = db.execute(
            sa_text("SELECT count(*) FROM chat_messages WHERE session_id = :sid AND role = 'user'"),
            {"sid": session_id},
        ).scalar()
        asst_msgs = db.execute(
            sa_text("SELECT count(*) FROM chat_messages WHERE session_id = :sid AND role = 'assistant'"),
            {"sid": session_id},
        ).scalar()
        assert user_msgs == 1
        assert asst_msgs == 1

