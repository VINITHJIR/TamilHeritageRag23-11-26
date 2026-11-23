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


LEVEL1_SITE_IDS = {
    "TN-CGL-0001", "TN-CHN-0001", "TN-ERD-0001", "TN-KNK-0001", "TN-KNK-0002",
    "TN-MDU-0001", "TN-SVG-0001", "TN-TNJ-0001", "TN-TNJ-0002", "TN-VPM-0001",
}
LEVEL1_CATEGORIES = {
    "temple", "monument", "museum", "fort", "palace", "rock_cut", "archaeological_site",
    "memorial", "heritage_tourist_spot", "unesco", "asi_protected",
}

# Tables that later levels only ever grow; snapshot them for idempotency checks.
COUNTED_TABLES = [
    "sites", "site_aliases", "categories", "locations", "chunk_registry",
    "chat_messages", "message_citations",
]


def _all_qdrant_ids() -> set:
    """Scroll the whole collection (paginated; Level 1B has far more than 100 points)."""
    qdrant = get_qdrant_client()
    ids, offset = set(), None
    while True:
        points, offset = qdrant.scroll(
            collection_name=settings.QDRANT_COLLECTION, limit=1000, offset=offset,
            with_payload=False, with_vectors=False,
        )
        ids.update(str(p.id) for p in points)
        if offset is None:
            return ids


def _snapshot() -> dict:
    with SessionLocal() as db:
        counts = {t: db.execute(sa_text(f"SELECT count(*) FROM {t}")).scalar() for t in COUNTED_TABLES}
    counts["qdrant_points"] = get_qdrant_client().get_collection(settings.QDRANT_COLLECTION).points_count
    return counts


def test_database_and_vector_counts():
    """Level 1 seed is intact (later levels may only add) and PG/Qdrant ids match exactly."""
    with SessionLocal() as db:
        site_ids = {r[0] for r in db.execute(sa_text("SELECT site_id FROM sites"))}
        assert LEVEL1_SITE_IDS <= site_ids, f"Missing Level 1 sites: {LEVEL1_SITE_IDS - site_ids}"

        cats = {r[0] for r in db.execute(sa_text("SELECT code FROM categories"))}
        assert LEVEL1_CATEGORIES <= cats, f"Missing Level 1 categories: {LEVEL1_CATEGORIES - cats}"

        seed_aliases = db.execute(sa_text(
            "SELECT count(*) FROM site_aliases WHERE site_id = ANY(:ids)"), {"ids": list(LEVEL1_SITE_IDS)}).scalar()
        assert seed_aliases >= 25, f"Expected >= 25 aliases on Level 1 sites, got {seed_aliases}"

        assert db.execute(sa_text("SELECT count(*) FROM locations")).scalar() >= 22
        assert db.execute(sa_text("SELECT count(*) FROM chunk_registry")).scalar() >= 30
        assert db.execute(sa_text("SELECT count(*) FROM chat_messages")).scalar() >= 16
        assert db.execute(sa_text("SELECT count(*) FROM message_citations")).scalar() >= 11

        db_chunk_ids = {str(r[0]) for r in db.execute(sa_text("SELECT chunk_id FROM chunk_registry"))}

    qdrant_point_ids = _all_qdrant_ids()
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

    Compares a before/after snapshot, so it stays valid as Level 1B adds data.
    """
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    before = _snapshot()
    result = subprocess.run(
        [sys.executable, str(project_root / "server" / "scripts" / "seed.py")],
        capture_output=True, text=True, cwd=str(project_root),
    )
    # seed.py exits 0 even when rows already exist (idempotent by design)
    assert result.returncode == 0, f"seed.py re-run failed:\n{result.stderr}"
    after = _snapshot()
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, f"Counts changed after re-seed (before, after): {changed}"
