"""Unit tests for domain tiering service (server/app/services/domain_tiers.py)."""

import uuid
from app.db.postgres import SessionLocal
from app.services.domain_tiers import (
    extract_domain,
    get_domain_tier,
    is_tier_a_host,
    is_tier_b_host,
    update_domain_reliability,
)


def test_extract_domain():
    assert extract_domain("https://asi.nic.in/monuments/erode") == "asi.nic.in"
    assert extract_domain("http://www.thehindu.com:8080/news") == "thehindu.com"
    assert extract_domain("en.wikipedia.org/wiki/Pariyur") == "en.wikipedia.org"


def test_tier_rules_hosts():
    assert is_tier_a_host("asi.nic.in") is True
    assert is_tier_a_host("asichennai.gov.in") is True
    assert is_tier_a_host("tnarch.gov.in") is True
    assert is_tier_a_host("wikipedia.org") is False

    assert is_tier_b_host("iitm.ac.in") is True
    assert is_tier_b_host("history.harvard.edu") is True
    assert is_tier_b_host("randomblog.com") is False


def test_get_domain_tier():
    with SessionLocal() as db:
        # Tier A government rule
        tier_a, prior_a = get_domain_tier("https://asichennai.gov.in/monument", db=db)
        assert tier_a == "A"
        assert prior_a == 1.0

        # Tier B seeded domain
        tier_b, prior_b = get_domain_tier("https://en.wikipedia.org/wiki/Pariyur", db=db)
        assert tier_b == "B"
        assert prior_b == 0.6

        # Tier C unknown fallback
        tier_c, prior_c = get_domain_tier("https://unregistered-traveler-blog-77.org/temples", db=db)
        assert tier_c == "C"
        assert prior_c == 0.25


def test_update_domain_reliability():
    with SessionLocal() as db:
        domain = f"test-blog-{uuid.uuid4().hex[:8]}.org"
        # Initial reliability: alpha=2, beta=1 -> 2/3 = 0.6667
        # Record accurate observation: alpha becomes 3, beta 1 -> 3/4 = 0.75
        rel1 = update_domain_reliability(domain, accurate=True, db=db)
        assert rel1 == 0.75

        # Record inaccurate observation: beta becomes 2 -> 3/5 = 0.60
        rel2 = update_domain_reliability(domain, accurate=False, db=db)
        assert rel2 == 0.60
