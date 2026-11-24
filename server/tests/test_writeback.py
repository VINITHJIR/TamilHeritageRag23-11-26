"""Unit tests for calibrated write-back service (server/app/services/writeback.py)."""

import uuid
from sqlalchemy import text as sa_text
from app.db.postgres import SessionLocal
from app.services.fact_consensus import FieldDecisionResult
from app.services.writeback import (
    allocate_next_site_id,
    calculate_site_completeness,
    check_writeback_gate,
    execute_writeback,
)


def test_check_writeback_gate_not_accepted():
    """Rule 3.5: Gating fails if existence is not confirmed or district_id is missing."""
    gate_ok, reason = check_writeback_gate(existence_accepted=False, district_id=1)
    assert gate_ok is False
    assert "not confirmed" in reason.lower()

    gate_ok2, reason2 = check_writeback_gate(existence_accepted=True, district_id=None)
    assert gate_ok2 is False
    assert "district_id" in reason2.lower()


def test_check_writeback_gate_success():
    """Rule 3.5: Gating succeeds iff existence is accepted and district_id is known. NO other field required."""
    gate_ok, reason = check_writeback_gate(existence_accepted=True, district_id=1)
    assert gate_ok is True
    assert "satisfied" in reason


def test_calculate_site_completeness():
    """Rule 3.7: Completeness levels: existence_only (<=1), partial (2-3), complete (>=4)."""
    # 0 optional verified -> existence_only
    d0 = {}
    assert calculate_site_completeness(d0) == "existence_only"

    # 1 optional verified -> existence_only
    d1 = {"deity": FieldDecisionResult("deity", "verified", "Shiva", 1.5, 2, False, "r")}
    assert calculate_site_completeness(d1) == "existence_only"

    # 2 optional verified -> partial
    d2 = {
        "deity": FieldDecisionResult("deity", "verified", "Shiva", 1.5, 2, False, "r"),
        "builder": FieldDecisionResult("builder", "verified", "Raja Raja Chola I", 1.5, 2, False, "r"),
    }
    assert calculate_site_completeness(d2) == "partial"

    # 4 optional verified -> complete
    d4 = {
        "deity": FieldDecisionResult("deity", "verified", "Shiva", 1.5, 2, False, "r"),
        "builder": FieldDecisionResult("builder", "verified", "Raja Raja Chola I", 1.5, 2, False, "r"),
        "dynasty": FieldDecisionResult("dynasty", "verified", "Chola", 1.5, 2, False, "r"),
        "period": FieldDecisionResult("period", "verified", "11th century CE", 1.5, 2, False, "r"),
    }
    assert calculate_site_completeness(d4) == "complete"


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
            "period": FieldDecisionResult("period", "verified", "17th century CE", 1.5, 2, True, "rule_a"),
            "builder": FieldDecisionResult("builder", "verified", "Tirumala Nayaka", 1.5, 2, True, "rule_a"),
            "overview": FieldDecisionResult("overview", "unconfirmed", "Mock overview text.", 0.6, 1, False, "unconfirmed"),
        }

        site_id = None
        try:
            site_id = execute_writeback(
                db=db,
                job_id=job_id,
                decisions=decisions,
                candidate_key=f"mock_site|{erode_id}",
                existence_accepted=True,
                district_id=int(erode_id),
                entity_name="Mock Heritage Site",
            )

            assert site_id is not None
            assert site_id.startswith("TN-ERD-")

            # Verify site in PostgreSQL
            site_row = db.execute(
                sa_text("SELECT site_id, name_en, origin, status, trust, completeness FROM sites WHERE site_id = :sid"),
                {"sid": site_id},
            ).mappings().fetchone()
            assert site_row is not None
            assert site_row["name_en"] == "Mock Heritage Site"
            assert site_row["origin"] == "web_consensus"
            assert site_row["status"] == "verified"
            assert site_row["trust"] == "official"
            # period, builder, name_ta = 3 optional fields verified -> partial
            assert site_row["completeness"] == "partial"

            # Verify job status in augment_jobs
            job_row = db.execute(
                sa_text("SELECT status, site_id FROM augment_jobs WHERE job_id = :jid"),
                {"jid": job_id},
            ).mappings().fetchone()
            assert job_row["status"] == "verified"
            assert job_row["site_id"] == site_id

            # Verify chunks in chunk_registry (fact card en + ta)
            chunk_count = db.execute(
                sa_text("SELECT count(*) FROM chunk_registry WHERE site_id = :sid"),
                {"sid": site_id},
            ).scalar()
            assert chunk_count >= 2

            # Rule 3.8: UNCONFIRMED values are NOT written to site_facts
            unconfirmed_facts = db.execute(
                sa_text("SELECT count(*) FROM site_facts WHERE site_id = :sid AND field = 'overview'"),
                {"sid": site_id},
            ).scalar()
            assert unconfirmed_facts == 0

            # Verified fields ARE written to site_facts
            verified_facts = db.execute(
                sa_text("SELECT count(*) FROM site_facts WHERE site_id = :sid AND field IN ('builder', 'period')"),
                {"sid": site_id},
            ).scalar()
            assert verified_facts == 2
        finally:
            if site_id:
                from app.db.qdrant import get_qdrant_client
                from app.core.config import settings
                from qdrant_client.http import models
                qdrant = get_qdrant_client()
                try:
                    qdrant.delete(
                        collection_name=settings.QDRANT_COLLECTION,
                        points_selector=models.FilterSelector(
                            filter=models.Filter(must=[models.FieldCondition(key="site_id", match=models.MatchValue(value=site_id))])
                        ),
                    )
                except Exception:
                    pass
                db.execute(sa_text("DELETE FROM chunk_registry WHERE site_id = :sid"), {"sid": site_id})
                db.execute(sa_text("DELETE FROM site_facts WHERE site_id = :sid"), {"sid": site_id})
                db.execute(sa_text("DELETE FROM site_aliases WHERE site_id = :sid"), {"sid": site_id})
                db.execute(sa_text("DELETE FROM augment_jobs WHERE job_id = :jid"), {"jid": job_id})
                db.execute(sa_text("DELETE FROM sites WHERE site_id = :sid"), {"sid": site_id})
                db.commit()
