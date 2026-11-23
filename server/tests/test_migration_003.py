"""Verification tests for Migration 003 (Self-Augmentation schema)."""

from sqlalchemy import text as sa_text
from app.db.postgres import SessionLocal


def test_source_domains_seeded():
    """Verify source_domains table structure and initial seeds."""
    with SessionLocal() as db:
        rows = db.execute(sa_text("SELECT domain, tier, prior, alpha, beta FROM source_domains ORDER BY domain")).mappings().fetchall()
        domain_map = {r["domain"]: r for r in rows}

        # Check count (at least 15 domains seeded)
        assert len(rows) >= 14

        # Check Tier A domains
        tier_a_domains = [
            "asi.nic.in", "asichennai.gov.in", "tnarch.gov.in", "hrce.tn.gov.in", "tamilnadutourism.tn.gov.in",
            "whc.unesco.org", "ignca.gov.in", "tamildigitallibrary.in", "abhilekh-patal.in", "bharatshri.asi.gov.in"
        ]
        for d in tier_a_domains:
            assert d in domain_map
            assert domain_map[d]["tier"] == "A"
            assert float(domain_map[d]["prior"]) == 1.0

        # Check Tier B domains
        tier_b_domains = [
            "wikipedia.org", "wikidata.org", "thehindu.com", "newindianexpress.com", "timesofindia.indiatimes.com"
        ]
        for d in tier_b_domains:
            assert d in domain_map
            assert domain_map[d]["tier"] == "B"
            assert float(domain_map[d]["prior"]) == 0.6


def test_augment_tables_exist():
    """Verify augment_jobs, augment_sources, evidence_ledger, and field_decisions tables exist."""
    with SessionLocal() as db:
        tables = [
            r[0] for r in db.execute(sa_text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )).fetchall()
        ]
        assert "source_domains" in tables
        assert "augment_jobs" in tables
        assert "augment_sources" in tables
        assert "evidence_ledger" in tables
        assert "field_decisions" in tables


def test_schema_modifications():
    """Verify site_facts.decision and query_logs path_detail/gemini_calls columns."""
    with SessionLocal() as db:
        site_facts_cols = [
            r[0] for r in db.execute(sa_text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'site_facts'"
            )).fetchall()
        ]
        assert "decision" in site_facts_cols

        query_logs_cols = [
            r[0] for r in db.execute(sa_text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'query_logs'"
            )).fetchall()
        ]
        assert "path_detail" in query_logs_cols
        assert "gemini_calls" in query_logs_cols
