"""Unit tests for fact consensus engine (server/app/services/fact_consensus.py)."""

from app.services.fact_consensus import (
    evaluate_field_consensus,
    normalize_builder,
    normalize_deity,
    normalize_fact_value,
    normalize_year,
)


def test_normalizers():
    # Year normalizer
    assert normalize_year("1010 CE") == "1010 CE"
    assert normalize_year("Built around 1010 AD") == "1010 CE"
    assert normalize_year("11th century") == "11th century CE"

    # Builder normalizer
    assert normalize_builder("Rajaraja Cholan") == "Raja Raja Chola I"
    assert normalize_builder("king rajaraja chola") == "Raja Raja Chola I"
    assert normalize_builder("Karikalan") == "Karikala Chola"

    # Deity normalizer
    assert normalize_deity("Lord Shiva") == "Shiva"
    assert normalize_deity("Peruvudaiyar") == "Brihadeeswarar (Shiva)"
    assert normalize_deity("Brihadeeswarar") == "Brihadeeswarar (Shiva)"


def test_consensus_tier_a_authoritative():
    """Rule 1: Official Tier A claim with prior 1.0 -> verified."""
    claims = [
        {
            "norm_value": "Raja Raja Chola I",
            "raw_value": "Raja Raja Chola I",
            "tier": "A",
            "domain_prior": 1.0,
            "domain": "asi.nic.in",
            "cluster_key": "asi_1",
            "extractor_agreement": True,
        }
    ]
    res = evaluate_field_consensus("builder", claims)
    assert res.decision == "verified"
    assert res.value == "Raja Raja Chola I"
    assert res.rule_fired == "verified_tier_a_authoritative"
    assert res.has_tier_a is True


def test_consensus_multi_cluster_without_tier_a():
    """Rule 2: >= 2 distinct clusters with support >= 1.4 -> verified."""
    claims = [
        {
            "norm_value": "17th century CE",
            "tier": "B",
            "domain_prior": 0.8,
            "domain": "siteA.com",
            "cluster_key": "cluster_1",
            "extractor_agreement": True,
        },
        {
            "norm_value": "17th century CE",
            "tier": "B",
            "domain_prior": 0.7,
            "domain": "siteB.com",
            "cluster_key": "cluster_2",
            "extractor_agreement": True,
        },
    ]
    # support = 0.8 + 0.7 = 1.5 >= 1.4 across 2 clusters
    res = evaluate_field_consensus("period", claims)
    assert res.decision == "verified"
    assert res.value == "17th century CE"
    assert res.rule_fired == "verified_multi_cluster_consensus"
    assert res.n_clusters == 2


def test_copycat_discount():
    """Claims from the same cluster_key are discounted: only the maximum weight is counted."""
    claims = [
        {
            "norm_value": "Nayaka",
            "tier": "B",
            "domain_prior": 0.6,
            "cluster_key": "copycat_cluster_1",
            "extractor_agreement": True,
        },
        {
            "norm_value": "Nayaka",
            "tier": "B",
            "domain_prior": 0.6,
            "cluster_key": "copycat_cluster_1",  # Same cluster!
            "extractor_agreement": True,
        },
        {
            "norm_value": "Nayaka",
            "tier": "B",
            "domain_prior": 0.6,
            "cluster_key": "copycat_cluster_1",  # Same cluster!
            "extractor_agreement": True,
        },
    ]
    res = evaluate_field_consensus("dynasty", claims)
    # Since all 3 are from copycat_cluster_1, support should be 0.6, NOT 1.8!
    assert res.support == 0.6
    assert res.decision == "rejected"
    assert res.rule_fired == "rejected_insufficient_support"


def test_conflict_detection():
    """Rule 3: Competing values both having support >= 0.8 trigger a conflict."""
    claims = [
        # Candidate 1: Chola (support = 0.9)
        {
            "norm_value": "Chola",
            "tier": "B",
            "domain_prior": 0.9,
            "cluster_key": "cluster_1",
            "extractor_agreement": True,
        },
        # Candidate 2: Pandya (support = 0.85)
        {
            "norm_value": "Pandya",
            "tier": "B",
            "domain_prior": 0.85,
            "cluster_key": "cluster_2",
            "extractor_agreement": True,
        },
    ]
    res = evaluate_field_consensus("dynasty", claims)
    assert res.decision == "conflict"
    assert res.rule_fired == "conflict_competing_high_support"
    assert len(res.competing) >= 1
