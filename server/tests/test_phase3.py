"""Phase 3 Test Suite: Existence rule, field consensus, write-back gate.

Tests:
- 1 tier-A page -> verified site, completeness 'existence_only'.
- Only 2 tier-C clusters -> existence not accepted.
- Fake "Sri Kaviyan Temple, Erode" -> not accepted.
- 5 copied blogs vs 1 tier-A -> tier-A wins, verified.
- Two tier-A disagree -> conflict.
- Ratio exactly 0.80 and weight_sum >= 1.2 -> verified.
- Weight_sum exactly 1.2 and ratio >= 0.80 -> verified.
- "1010 CE" vs "11th century" merged -> "1010 CE".
- sites.completeness levels (existence_only, partial, complete).
- UNCONFIRMED values are not written to site_facts or chunks.
"""

import uuid
from typing import Any, Dict, List
import pytest
from sqlalchemy import text as sa_text

from app.db.postgres import SessionLocal
from app.db.qdrant import get_qdrant_client
from app.core.config import settings
from qdrant_client.http import models
from app.services.spec_config import DecisionCode, OPTIONAL_FIELDS
from app.services.augment_orchestrator import check_existence
from app.services.fact_consensus import evaluate_field_consensus, FieldDecisionResult
from app.services.writeback import (
    calculate_site_completeness,
    execute_writeback,
    check_writeback_gate,
)


# -----------------------------------------------------------------------------
# 1. Existence Check Tests (Rule 3.1, 3.2)
# -----------------------------------------------------------------------------
def test_rule_3_2_one_tier_a_page_existence_accepted():
    """Rule 3.2: 1 tier-A cluster mentioning entity AND place/district is accepted."""
    clustered_sources = [
        {
            "cluster_key": "asi.nic.in",
            "url": "https://asi.nic.in/monument_detail",
            "tier": "A",
            "text": "Shore Temple is an ancient heritage temple located in Mamallapuram.",
        }
    ]
    accepted, reason, info = check_existence(
        clustered_sources=clustered_sources,
        entity_name="Shore Temple",
        district_name="Mamallapuram",
    )
    assert accepted is True
    assert reason == "ACCEPTED"
    assert info["tier_a_clusters"] == 1


def test_rule_3_2_only_two_tier_c_clusters_not_accepted():
    """Rule 3.2: Only 2 tier-C clusters mentioning entity and place is NOT accepted.

    Requires >= 1 tier-A OR >= 3 independent clusters.
    """
    clustered_sources = [
        {
            "cluster_key": "travelblog1.com",
            "url": "https://travelblog1.com/temple",
            "tier": "C",
            "text": "Obscure Kovil is a small village temple in Erode district.",
        },
        {
            "cluster_key": "travelblog2.com",
            "url": "https://travelblog2.com/temple",
            "tier": "C",
            "text": "Visiting Obscure Kovil near Erode was a pleasant trip.",
        },
    ]
    accepted, reason, info = check_existence(
        clustered_sources=clustered_sources,
        entity_name="Obscure Kovil",
        district_name="Erode",
    )
    assert accepted is False
    assert reason == DecisionCode.EXISTENCE_NOT_CONFIRMED.value
    assert info["independent_clusters"] == 2
    assert info["tier_a_clusters"] == 0


def test_rule_3_2_fake_sri_kaviyan_temple_erode_not_accepted():
    """Rule 3.2: Fabricated 'Sri Kaviyan Temple in Erode' has no reliable clusters and is not accepted."""
    clustered_sources = [
        {
            "cluster_key": "random-social.com",
            "url": "https://random-social.com/post/123",
            "tier": "C",
            "text": "Someone asked about Sri Kaviyan Temple on social media.",  # Does not mention Erode
        }
    ]
    accepted, reason, info = check_existence(
        clustered_sources=clustered_sources,
        entity_name="Sri Kaviyan Temple",
        district_name="Erode",
    )
    assert accepted is False
    assert reason == DecisionCode.EXISTENCE_NOT_CONFIRMED.value


def test_rule_3_2_three_independent_tier_c_clusters_accepted():
    """Rule 3.2: >= 3 independent clusters mentioning entity and district are accepted."""
    clustered_sources = [
        {
            "cluster_key": "source1.org",
            "url": "https://source1.org/temple",
            "tier": "C",
            "text": "Local Grama Kovil is situated in Salem district.",
        },
        {
            "cluster_key": "source2.com",
            "url": "https://source2.com/temple",
            "tier": "C",
            "text": "Salem features the ancient Local Grama Kovil shrine.",
        },
        {
            "cluster_key": "source3.net",
            "url": "https://source3.net/temple",
            "tier": "C",
            "text": "Worshippers at Local Grama Kovil in Salem celebrate annual festivities.",
        },
    ]
    accepted, reason, info = check_existence(
        clustered_sources=clustered_sources,
        entity_name="Local Grama Kovil",
        district_name="Salem",
    )
    assert accepted is True
    assert info["independent_clusters"] == 3


# -----------------------------------------------------------------------------
# 2. Field Consensus Tests (Rule 3.3, 3.4)
# -----------------------------------------------------------------------------
def test_rule_3_3_five_copied_blogs_vs_one_tier_a_tier_a_wins():
    """Rule 3.3: 5 copied blogs (1 cluster, tier-C) vs 1 tier-A cluster + 1 tier-B -> tier-A value wins."""
    claims = [
        # 5 copied blogs sharing cluster_id=1 with weight = 0.25
        {
            "cluster_id": 1,
            "raw_value": "Mythical King",
            "norm_value": "Mythical King",
            "tier": "C",
            "domain_prior": 0.25,
            "domain_reliability": 1.0,
            "evidence_quote": "Mythical King built it",
            "quote_found": True,
        },
        {
            "cluster_id": 1,
            "raw_value": "Mythical King",
            "norm_value": "Mythical King",
            "tier": "C",
            "domain_prior": 0.25,
            "domain_reliability": 1.0,
            "evidence_quote": "Mythical King built it",
            "quote_found": True,
        },
        # Tier-A cluster (weight 1.0) and Tier-B cluster (weight 0.6)
        {
            "cluster_id": 2,
            "raw_value": "Raja Raja Chola I",
            "norm_value": "Raja Raja Chola I",
            "tier": "A",
            "domain_prior": 1.0,
            "domain_reliability": 1.0,
            "evidence_quote": "Raja Raja Chola I constructed",
            "quote_found": True,
        },
        {
            "cluster_id": 3,
            "raw_value": "Raja Raja Chola I",
            "norm_value": "Raja Raja Chola I",
            "tier": "B",
            "domain_prior": 0.6,
            "domain_reliability": 1.0,
            "evidence_quote": "Raja Raja Chola I patronized",
            "quote_found": True,
        },
    ]
    dec = evaluate_field_consensus("builder", claims)
    assert dec.decision == "verified"
    assert dec.value == "Raja Raja Chola I"
    # Weight of Raja Raja Chola = 1.0 + 0.6 = 1.6; Mythical King = 0.25; Total = 1.85
    # Ratio = 1.6 / 1.85 = 0.865 >= 0.80
    assert dec.support >= 1.2
    assert dec.support_ratio >= 0.80


def test_rule_3_3_two_tier_a_disagree_produces_conflict():
    """Rule 3.3: Two tier-A clusters disagreeing produces 'conflict' status."""
    claims = [
        {
            "cluster_id": 1,
            "raw_value": "1010 CE",
            "norm_value": "1010 CE",
            "tier": "A",
            "domain_prior": 1.0,
            "domain_reliability": 1.0,
            "evidence_quote": "completed in 1010 CE",
            "quote_found": True,
        },
        {
            "cluster_id": 2,
            "raw_value": "1250 CE",
            "norm_value": "1250 CE",
            "tier": "A",
            "domain_prior": 1.0,
            "domain_reliability": 1.0,
            "evidence_quote": "completed in 1250 CE",
            "quote_found": True,
        },
    ]
    dec = evaluate_field_consensus("built_year", claims)
    assert dec.decision == "conflict"
    # Weight sum across competing values = 2.0 >= 1.2, but top ratio is 1.0 / 2.0 = 0.50 (< 0.80)
    total_weight = dec.support + sum(c["support"] for c in dec.competing)
    assert total_weight >= 1.2
    assert dec.support_ratio < 0.80
    assert len(dec.competing) == 1


def test_rule_3_3_ratio_exactly_point_80_and_weight_sum_ge_1_point_2_verified():
    """Rule 3.3 boundary check: ratio exactly 0.80 and weight_sum >= 1.2 -> verified."""
    # Top value weight = 1.6, competing value weight = 0.4 -> total = 2.0, ratio = 1.6 / 2.0 = 0.80
    claims = [
        # Top value from cluster 1 (1.0) and cluster 2 (0.6)
        {
            "cluster_id": 1,
            "raw_value": "Chola",
            "norm_value": "Chola",
            "tier": "A",
            "domain_prior": 1.0,
            "domain_reliability": 1.0,
            "evidence_quote": "Chola period",
            "quote_found": True,
        },
        {
            "cluster_id": 2,
            "raw_value": "Chola",
            "norm_value": "Chola",
            "tier": "B",
            "domain_prior": 0.6,
            "domain_reliability": 1.0,
            "evidence_quote": "Chola dynasty",
            "quote_found": True,
        },
        # Competing value with cluster weight = 0.4
        {
            "cluster_id": 3,
            "raw_value": "Pandya",
            "norm_value": "Pandya",
            "tier": "B",
            "domain_prior": 0.4,
            "domain_reliability": 1.0,
            "evidence_quote": "Pandya rulers",
            "quote_found": True,
        },
    ]
    dec = evaluate_field_consensus("dynasty", claims)
    assert dec.decision == "verified"
    assert dec.value == "Chola"
    assert dec.support_ratio == pytest.approx(0.80, abs=1e-4)
    assert dec.support == pytest.approx(1.6, abs=1e-4)


def test_rule_3_3_weight_sum_exactly_1_point_2_and_ratio_ge_point_80_verified():
    """Rule 3.3 boundary check: weight_sum exactly 1.2 and ratio >= 0.80 -> verified."""
    # Two tier-B clusters (0.6 + 0.6 = 1.2) for Lord Shiva
    claims = [
        {
            "cluster_id": 1,
            "raw_value": "Lord Shiva",
            "norm_value": "Lord Shiva",
            "tier": "B",
            "domain_prior": 0.6,
            "domain_reliability": 1.0,
            "evidence_quote": "Lord Shiva",
            "quote_found": True,
        },
        {
            "cluster_id": 2,
            "raw_value": "Lord Shiva",
            "norm_value": "Lord Shiva",
            "tier": "B",
            "domain_prior": 0.6,
            "domain_reliability": 1.0,
            "evidence_quote": "Lord Shiva is worshipped",
            "quote_found": True,
        },
    ]
    dec = evaluate_field_consensus("deity", claims)
    assert dec.decision == "verified"
    assert dec.value == "Shiva"
    assert dec.support == pytest.approx(1.2, abs=1e-4)
    assert dec.support_ratio == 1.0


def test_rule_3_3_merges_year_inside_century():
    """Rule 3.3: '1010 CE' vs '11th century' merged -> '1010 CE'."""
    claims = [
        {
            "cluster_id": 1,
            "raw_value": "1010 CE",
            "norm_value": "1010 CE",
            "tier": "A",
            "domain_prior": 1.0,
            "domain_reliability": 1.0,
            "evidence_quote": "in 1010 CE",
            "quote_found": True,
        },
        {
            "cluster_id": 2,
            "raw_value": "11th century CE",
            "norm_value": "11th century CE",
            "tier": "B",
            "domain_prior": 0.6,
            "domain_reliability": 1.0,
            "evidence_quote": "11th century CE",
            "quote_found": True,
        },
    ]
    dec = evaluate_field_consensus("built_year", claims)
    assert dec.decision == "verified"
    assert dec.value == "1010 CE"
    # Merged weight = 1.0 + 0.6 = 1.6
    assert dec.support == pytest.approx(1.6, abs=1e-4)


# -----------------------------------------------------------------------------
# 3. Completeness & Writeback Tests (Rule 3.5 - 3.8)
# -----------------------------------------------------------------------------
def test_rule_3_7_completeness_levels():
    """Rule 3.7: sites.completeness levels.

    <= 1 verified optional field -> 'existence_only'
    2 - 3 verified optional fields -> 'partial'
    >= 4 verified optional fields -> 'complete'
    """
    # 0 optional fields
    d_zero: Dict[str, FieldDecisionResult] = {}
    assert calculate_site_completeness(d_zero) == "existence_only"

    # 1 optional field (builder)
    d_one = {
        "builder": FieldDecisionResult(field="builder", decision="verified", value="Raja Raja Chola I", support=1.6, n_clusters=2, has_tier_a=True, rule_fired="verified_tier_a", support_ratio=1.0)
    }
    assert calculate_site_completeness(d_one) == "existence_only"

    # 2 optional fields (builder, deity)
    d_two = {
        "builder": FieldDecisionResult(field="builder", decision="verified", value="Raja Raja Chola I", support=1.6, n_clusters=2, has_tier_a=True, rule_fired="verified_tier_a", support_ratio=1.0),
        "deity": FieldDecisionResult(field="deity", decision="verified", value="Lord Shiva", support=1.2, n_clusters=2, has_tier_a=False, rule_fired="verified_multi_source", support_ratio=1.0),
    }
    assert calculate_site_completeness(d_two) == "partial"

    # 3 optional fields (builder, deity, dynasty)
    d_three = {
        "builder": FieldDecisionResult(field="builder", decision="verified", value="Raja Raja Chola I", support=1.6, n_clusters=2, has_tier_a=True, rule_fired="verified_tier_a", support_ratio=1.0),
        "deity": FieldDecisionResult(field="deity", decision="verified", value="Lord Shiva", support=1.2, n_clusters=2, has_tier_a=False, rule_fired="verified_multi_source", support_ratio=1.0),
        "dynasty": FieldDecisionResult(field="dynasty", decision="verified", value="Chola", support=1.2, n_clusters=2, has_tier_a=False, rule_fired="verified_multi_source", support_ratio=1.0),
    }
    assert calculate_site_completeness(d_three) == "partial"

    # 4 optional fields (builder, deity, dynasty, period)
    d_four = {
        "builder": FieldDecisionResult(field="builder", decision="verified", value="Raja Raja Chola I", support=1.6, n_clusters=2, has_tier_a=True, rule_fired="verified_tier_a", support_ratio=1.0),
        "deity": FieldDecisionResult(field="deity", decision="verified", value="Lord Shiva", support=1.2, n_clusters=2, has_tier_a=False, rule_fired="verified_multi_source", support_ratio=1.0),
        "dynasty": FieldDecisionResult(field="dynasty", decision="verified", value="Chola", support=1.2, n_clusters=2, has_tier_a=False, rule_fired="verified_multi_source", support_ratio=1.0),
        "period": FieldDecisionResult(field="period", decision="verified", value="11th century CE", support=1.2, n_clusters=2, has_tier_a=False, rule_fired="verified_multi_source", support_ratio=1.0),
    }
    assert calculate_site_completeness(d_four) == "complete"


def test_rule_3_5_writeback_gate_only_requires_existence_and_district():
    """Rule 3.5: check_writeback_gate passes iff existence_accepted is True and district_id is known."""
    # Pass: existence=True, district_id=5
    ok, reason = check_writeback_gate(existence_accepted=True, district_id=5)
    assert ok is True
    assert "satisfied" in reason

    # Fail: existence=False
    ok, reason = check_writeback_gate(existence_accepted=False, district_id=5)
    assert ok is False
    assert "Existence not confirmed" in reason

    # Fail: district_id=None
    ok, reason = check_writeback_gate(existence_accepted=True, district_id=None)
    assert ok is False
    assert "district_id" in reason


def test_rule_3_2_and_3_7_one_tier_a_page_verifies_site_existence_only_db():
    """Rule 3.2 & 3.7: 1 tier-A page -> verified site stored in PostgreSQL with completeness 'existence_only'."""
    unique_name = f"Test TierA Temple {uuid.uuid4().hex[:6]}"
    allocated_site_id = None
    test_job_id = uuid.uuid4()

    with SessionLocal() as db:
        # Get Thanjavur district ID
        d_row = db.execute(sa_text("SELECT location_id FROM locations WHERE level = 'district' AND lower(name_en) = 'thanjavur'")).fetchone()
        assert d_row is not None
        thanjavur_id = d_row[0]

        # Insert test augment job
        db.execute(
            sa_text("""
                INSERT INTO augment_jobs (job_id, query, candidate_key, entity_name, district_id, status, config, started_at)
                VALUES (:jid, 'Test query for TierA', :ck, :en, :did, 'running', '{}'::jsonb, now())
            """),
            {"jid": test_job_id, "ck": f"{unique_name}|{thanjavur_id}", "en": unique_name, "did": thanjavur_id},
        )
        db.commit()

        # Only existence accepted with 1 tier-A source; 0 optional verified fields
        decisions: Dict[str, FieldDecisionResult] = {
            "primary_category": FieldDecisionResult(
                field="primary_category", decision="verified", value="temple", support=1.0, n_clusters=1, has_tier_a=True, rule_fired="verified_tier_a", support_ratio=1.0
            )
        }

        allocated_site_id = execute_writeback(
            db=db,
            job_id=test_job_id,
            decisions=decisions,
            candidate_key=f"{unique_name}|{thanjavur_id}",
            existence_accepted=True,
            district_id=thanjavur_id,
            entity_name=unique_name,
            relevant_sources=[{
                "url": "https://asi.nic.in/unique_tier_a",
                "tier": "A",
                "prior": 1.0,
                "domain_reliability": 1.0,
                "text": f"{unique_name} is located in Thanjavur district.",
            }],
        )

        assert allocated_site_id is not None
        assert allocated_site_id.startswith("TN-TNJ-")

        # Verify sites row in PostgreSQL
        s_row = db.execute(
            sa_text("SELECT site_id, name_en, completeness, status, trust, origin FROM sites WHERE site_id = :sid"),
            {"sid": allocated_site_id},
        ).mappings().fetchone()

        assert s_row is not None
        assert s_row["name_en"] == unique_name
        assert s_row["status"] == "verified"
        assert s_row["completeness"] == "existence_only"
        assert s_row["trust"] == "official"
        assert s_row["origin"] == "web_consensus"

    # Hermetic DB & Qdrant cleanup
    if allocated_site_id:
        qdrant = get_qdrant_client()
        qdrant.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(key="site_id", match=models.MatchValue(value=allocated_site_id))])
            ),
        )
        with SessionLocal() as db:
            cids = [r[0] for r in db.execute(sa_text("SELECT chunk_id FROM chunk_registry WHERE site_id = :sid"), {"sid": allocated_site_id}).fetchall()]
            if cids:
                db.execute(sa_text("DELETE FROM message_citations WHERE chunk_id = ANY(:cids)"), {"cids": cids})
            db.execute(sa_text("DELETE FROM field_decisions WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM evidence_ledger WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM site_aliases WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM site_facts WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM chunk_registry WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM augment_jobs WHERE job_id = :jid OR site_id = :sid"), {"jid": test_job_id, "sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM sites WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.commit()


def test_rule_3_8_unconfirmed_values_not_written_to_site_facts_or_chunks():
    """Rule 3.8: UNCONFIRMED values are NOT written to site_facts or chunks."""
    unique_name = f"Test Unconfirmed Temple {uuid.uuid4().hex[:6]}"
    allocated_site_id = None
    test_job_id = uuid.uuid4()

    with SessionLocal() as db:
        # Get Chennai district ID
        d_row = db.execute(sa_text("SELECT location_id FROM locations WHERE level = 'district' AND lower(name_en) = 'chennai'")).fetchone()
        assert d_row is not None
        chennai_id = d_row[0]

        db.execute(
            sa_text("""
                INSERT INTO augment_jobs (job_id, query, candidate_key, entity_name, district_id, status, config, started_at)
                VALUES (:jid, 'Test query for Unconfirmed', :ck, :en, :did, 'running', '{}'::jsonb, now())
            """),
            {"jid": test_job_id, "ck": f"{unique_name}|{chennai_id}", "en": unique_name, "did": chennai_id},
        )
        db.commit()

        # builder is VERIFIED; timings and festivals are UNCONFIRMED
        decisions: Dict[str, FieldDecisionResult] = {
            "builder": FieldDecisionResult(
                field="builder", decision="verified", value="Raja Raja Chola I", support=1.6, n_clusters=2, has_tier_a=True, rule_fired="verified_tier_a", support_ratio=1.0
            ),
            "timings": FieldDecisionResult(
                field="timings", decision="unconfirmed", value="6:00 AM - 8:00 PM", support=0.6, n_clusters=1, has_tier_a=False, rule_fired="unconfirmed_partial_evidence", support_ratio=1.0
            ),
            "festivals": FieldDecisionResult(
                field="festivals", decision="unconfirmed", value="Chithirai Brahmotsavam", support=0.25, n_clusters=1, has_tier_a=False, rule_fired="unconfirmed_partial_evidence", support_ratio=1.0
            ),
        }

        allocated_site_id = execute_writeback(
            db=db,
            job_id=test_job_id,
            decisions=decisions,
            candidate_key=f"{unique_name}|{chennai_id}",
            existence_accepted=True,
            district_id=chennai_id,
            entity_name=unique_name,
            relevant_sources=[{
                "url": "https://asi.nic.in/unconfirmed_test",
                "tier": "A",
                "prior": 1.0,
                "domain_reliability": 1.0,
                "text": f"{unique_name} in Chennai was built by Raja Raja Chola I.",
            }],
        )

        assert allocated_site_id is not None

        # 1. Verify field_decisions has recorded all 3 decisions
        dec_rows = db.execute(
            sa_text("SELECT field, decision, value FROM field_decisions WHERE site_id = :sid"),
            {"sid": allocated_site_id},
        ).mappings().fetchall()
        dec_map = {r["field"]: r["decision"] for r in dec_rows}
        assert dec_map["builder"] == "verified"
        assert dec_map["timings"] == "unconfirmed"
        assert dec_map["festivals"] == "unconfirmed"

        # 2. Verify site_facts contains builder, but NOT timings or festivals
        fact_rows = db.execute(
            sa_text("SELECT field, value FROM site_facts WHERE site_id = :sid"),
            {"sid": allocated_site_id},
        ).mappings().fetchall()
        fact_fields = [r["field"] for r in fact_rows]
        assert "builder" in fact_fields
        assert "timings" not in fact_fields
        assert "festivals" not in fact_fields

        # 3. Verify Qdrant chunk payloads do NOT contain the unconfirmed values
        qdrant = get_qdrant_client()
        points, _ = qdrant.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            scroll_filter=models.Filter(must=[models.FieldCondition(key="site_id", match=models.MatchValue(value=allocated_site_id))]),
            with_payload=True,
            limit=10,
        )
        all_chunk_text = " ".join(p.payload.get("text", "") for p in points)
        assert "Raja Raja Chola I" in all_chunk_text
        assert "6:00 AM - 8:00 PM" not in all_chunk_text
        assert "Chithirai Brahmotsavam" not in all_chunk_text

    # Hermetic DB & Qdrant cleanup
    if allocated_site_id:
        qdrant = get_qdrant_client()
        qdrant.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(key="site_id", match=models.MatchValue(value=allocated_site_id))])
            ),
        )
        with SessionLocal() as db:
            cids = [r[0] for r in db.execute(sa_text("SELECT chunk_id FROM chunk_registry WHERE site_id = :sid"), {"sid": allocated_site_id}).fetchall()]
            if cids:
                db.execute(sa_text("DELETE FROM message_citations WHERE chunk_id = ANY(:cids)"), {"cids": cids})
            db.execute(sa_text("DELETE FROM field_decisions WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM evidence_ledger WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM site_aliases WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM site_facts WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM chunk_registry WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM augment_jobs WHERE job_id = :jid OR site_id = :sid"), {"jid": test_job_id, "sid": allocated_site_id})
            db.execute(sa_text("DELETE FROM sites WHERE site_id = :sid"), {"sid": allocated_site_id})
            db.commit()
