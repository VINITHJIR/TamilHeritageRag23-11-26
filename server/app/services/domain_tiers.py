"""Domain tiering and reliability scoring for TamilHeritage-GPT.

Implements Tier classification ('A'|'B'|'C'), domain priors, and
Bayesian Beta distribution updates for domain reliability:
reliability = alpha / (alpha + beta).
"""

import logging
from typing import Optional, Tuple
from urllib.parse import urlparse
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.db.postgres import SessionLocal

logger = logging.getLogger(__name__)

TIER_A_PRIOR = 1.0
TIER_B_PRIOR = 0.6
TIER_C_PRIOR = 0.25


def extract_domain(url_or_host: str) -> str:
    """Extract clean domain/hostname from a URL or raw domain string."""
    clean = url_or_host.strip().lower()
    if clean.startswith("http://") or clean.startswith("https://"):
        parsed = urlparse(clean)
        netloc = parsed.netloc.split(":")[0]  # strip port
    else:
        netloc = clean.split("/")[0].split(":")[0]

    # Strip leading 'www.'
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def is_tier_a_host(host: str) -> bool:
    """Check rule: *.nic.in or *.gov.in host = Tier A."""
    h = host.lower()
    return h.endswith(".nic.in") or h.endswith(".gov.in") or h == "nic.in" or h == "gov.in"


def is_tier_b_host(host: str) -> bool:
    """Check rule: *.ac.in or *.edu host = Tier B."""
    h = host.lower()
    return h.endswith(".ac.in") or h.endswith(".edu") or h == "ac.in" or h == "edu"


def get_domain_tier(url: str, db: Optional[Session] = None) -> Tuple[str, float]:
    """Determine domain tier and prior for a given URL.

    Rules:
    - Any *.nic.in or *.gov.in host = 'A' (prior 1.0).
    - Any *.ac.in or *.edu host = 'B' (prior 0.6).
    - Table source_domains lookup.
    - Fallback: insert as tier 'C' (prior 0.25).
    """
    domain = extract_domain(url)

    # 1. Government / Official rule
    if is_tier_a_host(domain):
        return ("A", TIER_A_PRIOR)

    # 2. Academic / Educational rule
    if is_tier_b_host(domain):
        return ("B", TIER_B_PRIOR)

    # 3. Database lookup
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        row = db.execute(
            sa_text("SELECT tier, prior FROM source_domains WHERE domain = :domain"),
            {"domain": domain},
        ).mappings().fetchone()

        if row:
            return (str(row["tier"]), float(row["prior"]))

        # Check subdomains (e.g. en.wikipedia.org -> wikipedia.org)
        parts = domain.split(".")
        if len(parts) > 2:
            root_domain = ".".join(parts[-2:])
            root_row = db.execute(
                sa_text("SELECT tier, prior FROM source_domains WHERE domain = :root"),
                {"root": root_domain},
            ).mappings().fetchone()
            if root_row:
                return (str(root_row["tier"]), float(root_row["prior"]))

        # Fallback: insert as Tier C
        db.execute(
            sa_text("""
                INSERT INTO source_domains (domain, tier, prior, alpha, beta, updated_at)
                VALUES (:domain, 'C', :prior, 2, 1, now())
                ON CONFLICT (domain) DO NOTHING
            """),
            {"domain": domain, "prior": TIER_C_PRIOR},
        )
        db.commit()
        return ("C", TIER_C_PRIOR)

    finally:
        if should_close:
            db.close()


def update_domain_reliability(domain: str, accurate: bool, db: Optional[Session] = None) -> float:
    """Update domain reliability via Beta distribution alpha/beta parameters.

    accurate=True  -> alpha += 1
    accurate=False -> beta += 1
    reliability = alpha / (alpha + beta)
    """
    clean_domain = extract_domain(domain)
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        row = db.execute(
            sa_text("SELECT alpha, beta FROM source_domains WHERE domain = :domain"),
            {"domain": clean_domain},
        ).mappings().fetchone()

        if row:
            new_alpha = float(row["alpha"]) + (1.0 if accurate else 0.0)
            new_beta = float(row["beta"]) + (0.0 if accurate else 1.0)
        else:
            new_alpha = 3.0 if accurate else 2.0
            new_beta = 1.0 if accurate else 2.0

        reliability = new_alpha / (new_alpha + new_beta)

        db.execute(
            sa_text("""
                INSERT INTO source_domains (domain, tier, prior, alpha, beta, updated_at)
                VALUES (:domain, 'C', :prior, :alpha, :beta, now())
                ON CONFLICT (domain)
                DO UPDATE SET
                    alpha = :alpha,
                    beta = :beta,
                    updated_at = now()
            """),
            {
                "domain": clean_domain,
                "prior": TIER_C_PRIOR,
                "alpha": new_alpha,
                "beta": new_beta,
            },
        )
        db.commit()
        return round(reliability, 4)

    finally:
        if should_close:
            db.close()
