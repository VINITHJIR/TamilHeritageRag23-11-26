"""Unit tests for the autonomous IC³-WB Augmentation Orchestrator (server/app/services/augment_orchestrator.py)."""

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch
import uuid
import pytest
from sqlalchemy import text as sa_text
from app.core.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import get_qdrant_client
from app.services.augment_orchestrator import (
    AugmentResult,
    normalize_candidate_key,
    run_augmentation_pipeline,
    strip_html_tags,
)


def test_strip_html_tags():
    html = "<html><body><h1>Pariyur Amman</h1><p>Ancient temple in <b>Erode</b>.</p><script>alert('x')</script></body></html>"
    text = strip_html_tags(html)
    assert "Pariyur Amman" in text
    assert "Ancient temple in Erode" in text
    assert "alert" not in text


def test_normalize_candidate_key():
    key = normalize_candidate_key("Pariyur Kondathu Kaliamman Temple", 5)
    assert key == "pariyur_kondathu_kaliamman_temple|5"

    key_none = normalize_candidate_key("Unknown Site", None)
    assert key_none == "unknown_site|unknown"


def test_orchestrator_non_heritage_rejection():
    """Non-heritage queries abort early without web fetching."""
    with SessionLocal() as db:
        res = run_augmentation_pipeline(
            query="What is the weather forecast today?",
            db=db,
        )
        assert res.status in ("rejected_nonexistent", "failed")
        assert res.site_id is None


def test_orchestrator_fail_insufficient_claims():
    """When web sources provide contradictory or insufficient claims, write-back gate rejects."""
    with SessionLocal() as db:
        unique_name = f"Ghost Temple {uuid.uuid4().hex[:6]}"

        # Mock search returning URLs
        def mock_search(query: str, max_results: int = 8) -> List[str]:
            return ["https://mock-blog-travel.org/ghost_temple"]

        # Mock page content
        def mock_fetcher(url: str) -> Optional[Tuple[str, int, str]]:
            return (
                f"{unique_name} is a rumoured structure with no known history, no inscriptions, and no records.",
                200,
                url,
            )

        # Mock claims: only overview, missing primary_category, history, builder, period
        def mock_claims(**kwargs) -> List[Dict[str, Any]]:
            return [
                {
                    "field": "overview",
                    "raw_value": f"{unique_name} is a rumoured structure.",
                    "norm_value": f"{unique_name} is a rumoured structure.",
                    "evidence_quote": f"{unique_name} is a rumoured structure",
                    "quote_found": True,
                }
            ]

        mock_classification = {
            "is_heritage": True,
            "entity_name": unique_name,
            "entity_name_ta": None,
            "place_name": "Nilgiris",
            "district_id": 11,
            "question_type": "history",
            "lang": "en",
        }

        res = None
        res2 = None
        try:
            with patch("app.services.augment_orchestrator.classify_query", return_value=mock_classification):
                res = run_augmentation_pipeline(
                    query=f"Tell me about {unique_name} in Nilgiris",
                    db=db,
                    search_fn=mock_search,
                    page_fetcher=mock_fetcher,
                    claims_extractor=mock_claims,
                )

                assert res.status in ("rejected_nonexistent", "failed")
                assert res.site_id is None
                assert "Missing required" in res.decision_reason

                # Test 24-hour budget rate-limit: second run for identical candidate_key returns skipped_budget
                res2 = run_augmentation_pipeline(
                    query=f"Tell me about {unique_name} in Nilgiris",
                    db=db,
                    search_fn=mock_search,
                    page_fetcher=mock_fetcher,
                    claims_extractor=mock_claims,
                )
                assert res2.status == "skipped_budget"
                assert "24-hour augmentation budget" in res2.decision_reason
        finally:
            if res:
                db.execute(sa_text("DELETE FROM augment_jobs WHERE job_id = :jid"), {"jid": res.job_id})
            if res2 and res2.job_id:
                db.execute(sa_text("DELETE FROM augment_jobs WHERE job_id = :jid"), {"jid": res2.job_id})
            db.commit()


def test_orchestrator_pass_verified_site():
    """Full end-to-end verification and writeback for a valid candidate site."""
    with SessionLocal() as db:
        # Find Chennai district location_id
        dist_row = db.execute(
            sa_text("SELECT location_id FROM locations WHERE level = 'district' AND lower(name_en) = 'chennai'")
        ).fetchone()
        assert dist_row is not None
        chennai_id = dist_row[0]

        mock_entity = f"Test Monument {uuid.uuid4().hex[:6]}"

        def mock_search(query: str, max_results: int = 8) -> List[str]:
            return [
                "https://asi.nic.in/test_monument",
                "https://en.wikipedia.org/wiki/Test_Monument",
            ]

        page_1_text = (
            f"{mock_entity} is a protected historical monument in Chennai. "
            f"It was built by King Raja Raja Chola I during the 11th century CE. "
            f"The monument contains ancient Chola granite architecture."
        )
        page_2_text = (
            f"{mock_entity} stands in central Chennai as a cultural landmark. "
            f"Historical records state that Raja Raja Chola I constructed this monument in 11th century CE. "
            f"The site features a spacious hall and ornate stone carvings."
        )

        def mock_fetcher(url: str) -> Optional[Tuple[str, int, str]]:
            if "asi.nic.in" in url:
                return (page_1_text, 200, url)
            return (page_2_text, 200, url)

        def mock_claims(**kwargs) -> List[Dict[str, Any]]:
            page_text = kwargs.get("page_text", "")
            claims = [
                {
                    "field": "primary_category",
                    "raw_value": "monument",
                    "norm_value": "monument",
                    "evidence_quote": "protected historical monument",
                    "quote_found": True,
                },
                {
                    "field": "overview",
                    "raw_value": f"{mock_entity} is a protected historical monument in Chennai.",
                    "norm_value": f"{mock_entity} is a protected historical monument in Chennai.",
                    "evidence_quote": f"{mock_entity} is a protected historical monument in Chennai",
                    "quote_found": True,
                },
                {
                    "field": "history",
                    "raw_value": "Constructed during the 11th century CE under Chola patronage.",
                    "norm_value": "Constructed during the 11th century CE under Chola patronage.",
                    "evidence_quote": "during the 11th century CE" if "during" in page_text else "constructed this monument in 11th century CE",
                    "quote_found": True,
                },
                {
                    "field": "builder",
                    "raw_value": "Raja Raja Chola I",
                    "norm_value": "Raja Raja Chola I",
                    "evidence_quote": "Raja Raja Chola I",
                    "quote_found": True,
                },
                {
                    "field": "period",
                    "raw_value": "11th century CE",
                    "norm_value": "11th century CE",
                    "evidence_quote": "11th century CE",
                    "quote_found": True,
                },
            ]
            return claims

        # Mock classify_query so it returns mock_entity and Chennai district
        mock_classification = {
            "is_heritage": True,
            "entity_name": mock_entity,
            "entity_name_ta": None,
            "place_name": "Chennai",
            "district_id": chennai_id,
            "question_type": "history",
            "lang": "en",
        }

        with patch("app.services.augment_orchestrator.classify_query", return_value=mock_classification):
            res = run_augmentation_pipeline(
                query=f"Tell me about {mock_entity} in Chennai",
                db=db,
                search_fn=mock_search,
                page_fetcher=mock_fetcher,
                claims_extractor=mock_claims,
            )

            assert res.status == "verified"
            assert res.site_id is not None
            assert res.site_id.startswith("TN-CHN-")
            assert "name_en" in res.verified_fields
            assert "primary_category" in res.verified_fields

            # Verify persisted in PostgreSQL
            site_row = db.execute(
                sa_text("SELECT site_id, name_en, origin, status FROM sites WHERE site_id = :sid"),
                {"sid": res.site_id},
            ).mappings().fetchone()
            assert site_row is not None
            assert site_row["name_en"] == mock_entity
            assert site_row["origin"] == "web_consensus"
            assert site_row["status"] == "verified"

            # Clean up test site from DB and Qdrant
            from app.db.qdrant import get_qdrant_client
            from qdrant_client.http import models
            qdrant = get_qdrant_client()
            qdrant.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=models.FilterSelector(
                    filter=models.Filter(must=[models.FieldCondition(key="site_id", match=models.MatchValue(value=res.site_id))])
                ),
            )
            db.execute(sa_text("DELETE FROM augment_jobs WHERE job_id = :jid"), {"jid": res.job_id})
            db.execute(sa_text("DELETE FROM sites WHERE site_id = :sid"), {"sid": res.site_id})
            db.commit()
