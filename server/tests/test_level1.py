"""Comprehensive pytest test suite for Level 1 of TamilHeritage-GPT.

Covers:
  1. Relational database counts and Qdrant points count.
  2. Entity and ID consistency (PostgreSQL chunk_registry vs Qdrant points).
  3. Multilingual semantic search queries:
     - "தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?" -> top-1 TN-TNJ-0001
     - "Who built the Big Temple in Thanjavur?" -> top-1 TN-TNJ-0001
     - "ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?" -> top-1 TN-ERD-0001
     - "where can I see Chola bronze statues" with category=museum -> top-1 TN-CHN-0001
  4. Fuzzy/trigram search:
     - /api/sites?q=mahabalipuram shore templ -> first result TN-CGL-0001
  5. Seeder idempotency (running seed.py twice does not change counts).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sa_text
from app.core.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import get_qdrant_client
from app.main import app

client = TestClient(app)


def test_database_and_vector_counts():
    """Verify table row counts and vector points counts match Level 1 specifications."""
    with SessionLocal() as db:
        sites_cnt = db.execute(sa_text("SELECT count(*) FROM sites")).scalar()
        aliases_cnt = db.execute(sa_text("SELECT count(*) FROM site_aliases")).scalar()
        categories_cnt = db.execute(sa_text("SELECT count(*) FROM categories")).scalar()
        locations_cnt = db.execute(sa_text("SELECT count(*) FROM locations")).scalar()
        chunk_reg_cnt = db.execute(sa_text("SELECT count(*) FROM chunk_registry")).scalar()
        messages_cnt = db.execute(sa_text("SELECT count(*) FROM chat_messages")).scalar()
        citations_cnt = db.execute(sa_text("SELECT count(*) FROM message_citations")).scalar()

        assert sites_cnt == 10, f"Expected 10 sites, got {sites_cnt}"
        assert aliases_cnt == 25, f"Expected 25 aliases, got {aliases_cnt}"
        assert categories_cnt == 11, f"Expected 11 categories, got {categories_cnt}"
        assert locations_cnt == 22, f"Expected 22 locations, got {locations_cnt}"
        assert chunk_reg_cnt == 30, f"Expected 30 chunk_registry rows, got {chunk_reg_cnt}"
        assert messages_cnt == 16, f"Expected 16 chat_messages, got {messages_cnt}"
        assert citations_cnt == 11, f"Expected 11 citations, got {citations_cnt}"

        # Verify Qdrant points count
        qdrant = get_qdrant_client()
        col_info = qdrant.get_collection(settings.QDRANT_COLLECTION)
        assert col_info.points_count == 30, f"Expected 30 Qdrant points, got {col_info.points_count}"

        # Verify identical point ID sets
        db_chunk_ids = {str(r[0]) for r in db.execute(sa_text("SELECT chunk_id FROM chunk_registry")).fetchall()}
        points, _ = qdrant.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            limit=100,
            with_payload=False,
            with_vectors=False
        )
        qdrant_point_ids = {str(p.id) for p in points}
        assert db_chunk_ids == qdrant_point_ids, "PostgreSQL chunk_registry IDs must match Qdrant point IDs exactly"


def test_search_tamil_big_temple():
    """Search: 'தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?' -> top-1 site_id TN-TNJ-0001"""
    response = client.post("/api/search", json={
        "query": "தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?",
        "top_k": 3
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) > 0
    top_result = data["results"][0]
    assert top_result["site_id"] == "TN-TNJ-0001", f"Expected TN-TNJ-0001, got {top_result['site_id']}"


def test_search_english_big_temple():
    """Search: 'Who built the Big Temple in Thanjavur?' -> top-1 site_id TN-TNJ-0001"""
    response = client.post("/api/search", json={
        "query": "Who built the Big Temple in Thanjavur?",
        "top_k": 3
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) > 0
    top_result = data["results"][0]
    assert top_result["site_id"] == "TN-TNJ-0001", f"Expected TN-TNJ-0001, got {top_result['site_id']}"


def test_search_kannada_bannari():
    """Search: 'ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?' -> top-1 site_id TN-ERD-0001"""
    response = client.post("/api/search", json={
        "query": "ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?",
        "top_k": 3
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) > 0
    top_result = data["results"][0]
    assert top_result["site_id"] == "TN-ERD-0001", f"Expected TN-ERD-0001, got {top_result['site_id']}"


def test_search_chola_bronze_statues_with_filter():
    """Search: 'where can I see Chola bronze statues' with filter category=museum -> top-1 TN-CHN-0001"""
    response = client.post("/api/search", json={
        "query": "where can I see Chola bronze statues",
        "top_k": 3,
        "filters": {"category": "museum"}
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) > 0
    top_result = data["results"][0]
    assert top_result["site_id"] == "TN-CHN-0001", f"Expected TN-CHN-0001, got {top_result['site_id']}"


def test_sites_fuzzy_search():
    """Endpoint: /api/sites?q=mahabalipuram shore templ -> first result TN-CGL-0001"""
    response = client.get("/api/sites?q=mahabalipuram shore templ")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sites"]) > 0
    first_site = data["sites"][0]
    assert first_site["site_id"] == "TN-CGL-0001", f"Expected TN-CGL-0001, got {first_site['site_id']}"


def test_seed_idempotency():
    """Running seed.py (all steps) a second time must NOT change any counts.

    This guards against accidental duplicate inserts across all seed steps.
    """
    import subprocess
    import sys

    # Determine the venv python that is running pytest
    python_exe = sys.executable

    result = subprocess.run(
        [python_exe, "server/scripts/seed.py"],
        capture_output=True,
        text=True,
        cwd="D:\\2026\\BIT\\PAPER\\TAMIL HERITAGE RAG",
    )
    # seed.py exits 0 even when rows already exist (idempotent by design)
    assert result.returncode == 0, f"seed.py re-run failed:\n{result.stderr}"

    with SessionLocal() as db:
        assert db.execute(sa_text("SELECT count(*) FROM sites")).scalar() == 10, "sites count changed after re-seed"
        assert db.execute(sa_text("SELECT count(*) FROM site_aliases")).scalar() == 25, "aliases count changed"
        assert db.execute(sa_text("SELECT count(*) FROM chunk_registry")).scalar() == 30, "chunk_registry count changed"
        assert db.execute(sa_text("SELECT count(*) FROM chat_messages")).scalar() == 16, "chat_messages count changed"
        assert db.execute(sa_text("SELECT count(*) FROM message_citations")).scalar() == 11, "citations count changed"

    qdrant = get_qdrant_client()
    info = qdrant.get_collection(settings.QDRANT_COLLECTION)
    assert info.points_count == 30, f"Qdrant points changed after re-seed: {info.points_count}"
