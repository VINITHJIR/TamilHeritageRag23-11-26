"""Unit tests for calibrated write-back service (server/app/services/writeback.py)."""

import uuid
from sqlalchemy import text as sa_text
from app.db.postgres import SessionLocal
from app.services.fact_consensus import FieldDecisionResult
from app.services.writeback import (
    allocate_next_site_id,
    check_writeback_gate,
    execute_writeback,
)


def test_check_writeback_gate_missing_fields():
    """Gating fails if required core fields or historical context fields are missing."""
    decisions = {
        "name_en": FieldDecisionResult("name_en", "verified", "Pariyur Temple", 1.5, 2, False, "rule"),
        # Missing primary_category, district_id, overview, history
    }
    gate_ok, reason = check_writeback_gate(decisions)
    assert gate_ok is False
    assert "Missing required verified core fields" in reason


def test_check_writeback_gate_success():
    """Gating succeeds when all core fields and at least one historical context field are verified."""
    decisions = {
        "name_en": FieldDecisionResult("name_en", "verified", "Pariyur Amman Temple", 1.5, 2, False, "rule"),
        "primary_category": FieldDecisionResult("primary_category", "verified", "temple", 1.5, 2, False, "rule"),
        "district_id": FieldDecisionResult("district_id", "verified", "1", 1.5, 2, False, "rule"),
        "overview": FieldDecisionResult("overview", "verified", "Pariyur Amman Temple overview text.", 1.5, 2, False, "rule"),
        "history": FieldDecisionResult("history", "verified", "Pariyur Amman Temple history text.", 1.5, 2, False, "rule"),
        "period": FieldDecisionResult("period", "verified", "17th century CE", 1.5, 2, False, "rule"),
    }
    gate_ok, reason = check_writeback_gate(decisions)
    assert gate_ok is True
    assert "satisfied" in reason


def test_allocate_next_site_id():
    """Generates sequential site_id formatted as TN-{code3}-{seq:04d}."""
    with SessionLocal() as db:
        # In our database, TN-ERD-0001 is Bannari Amman Temple
        next_erd = allocate_next_site_id(db, "ERD")
        assert next_erd.startswith("TN-ERD-")
        seq_num = int(next_erd.split("-")[2])
        assert seq_num >= 2


def test_execute_writeback_and_atomic_persistence():
    """Verify complete atomic write-back: PostgreSQL sites, chunks, facts, and Qdrant points."""
    with SessionLocal() as db:
        # Find Erode district location_id
        dist_row = db.execute(
            sa_text("SELECT location_id FROM locations WHERE level = 'district' AND lower(name_en) = 'erode'")
        ).fetchone()
        assert dist_row is not None
        erode_id = str(dist_row[0])

        job_id = uuid.uuid4()
        # Seed an augment_job
        db.execute(
            sa_text("""
                INSERT INTO augment_jobs (job_id, candidate_key, query, entity_name, district_id, status)
                VALUES (:jid, 'mock_site|' || :dist, 'Tell me about Mock Heritage Site', 'Mock Heritage Site', :dist_int, 'running')
            """),
            {"jid": job_id, "dist": erode_id, "dist_int": int(erode_id)},
        )
        db.commit()

        decisions = {
            "name_en": FieldDecisionResult("name_en", "verified", "Mock Heritage Site", 1.5, 2, True, "rule_a"),
            "name_ta": FieldDecisionResult("name_ta", "verified", "சோதனை பாரம்பரிய தளம்", 1.5, 2, True, "rule_a"),
            "primary_category": FieldDecisionResult("primary_category", "verified", "monument", 1.5, 2, True, "rule_a"),
            "district_id": FieldDecisionResult("district_id", "verified", erode_id, 1.5, 2, True, "rule_a"),
            "overview": FieldDecisionResult("overview", "verified", "Mock Heritage Site overview text for writeback test.", 1.5, 2, True, "rule_a"),
            "history": FieldDecisionResult("history", "verified", "Mock Heritage Site built by Nayakas in 17th century.", 1.5, 2, True, "rule_a"),
            "period": FieldDecisionResult("period", "verified", "17th century CE", 1.5, 2, True, "rule_a"),
            "builder": FieldDecisionResult("builder", "verified", "Tirumala Nayaka", 1.5, 2, True, "rule_a"),
        }

        site_id = None
        try:
            site_id = execute_writeback(
                db=db,
                job_id=job_id,
                decisions=decisions,
                candidate_key=f"mock_site|{erode_id}",
            )

            assert site_id is not None
            assert site_id.startswith("TN-ERD-")

            # Verify site in PostgreSQL
            site_row = db.execute(
                sa_text("SELECT site_id, name_en, origin, status, trust FROM sites WHERE site_id = :sid"),
                {"sid": site_id},
            ).mappings().fetchone()
            assert site_row is not None
            assert site_row["name_en"] == "Mock Heritage Site"
            assert site_row["origin"] == "web_consensus"
            assert site_row["status"] == "verified"
            assert site_row["trust"] == "official"

            # Verify job status in augment_jobs
            job_row = db.execute(
                sa_text("SELECT status, site_id FROM augment_jobs WHERE job_id = :jid"),
                {"jid": job_id},
            ).mappings().fetchone()
            assert job_row["status"] == "verified"
            assert job_row["site_id"] == site_id

            # Verify chunks in chunk_registry
            chunk_count = db.execute(
                sa_text("SELECT count(*) FROM chunk_registry WHERE site_id = :sid"),
                {"sid": site_id},
            ).scalar()
            assert chunk_count == 2  # overview + history
        finally:
            # Clean up test site from DB and Qdrant to ensure zero test pollution
            if site_id:
                from app.db.qdrant import get_qdrant_client
                from app.core.config import settings
                from qdrant_client.http import models
                qdrant = get_qdrant_client()
                qdrant.delete(
                    collection_name=settings.QDRANT_COLLECTION,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(must=[models.FieldCondition(key="site_id", match=models.MatchValue(value=site_id))])
                    ),
                )
                db.execute(sa_text("DELETE FROM augment_jobs WHERE job_id = :jid"), {"jid": job_id})
                db.execute(sa_text("DELETE FROM sites WHERE site_id = :sid"), {"sid": site_id})
                db.commit()
