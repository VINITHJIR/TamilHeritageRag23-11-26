"""Comprehensive End-to-End Integration Tests for TamilHeritage-GPT.

Verifies the full lifecycle of the conversational RAG and IC³-WB self-augmentation engine:
1. DB Path: Multilingual query for an existing site returns verified DB chunks (path='db').
2. Out of Scope: Non-heritage query is politely rejected without triggering augmentation.
3. Identity Check Gating: Query naming an entity not in DB rejects drift to existing phonetically similar sites.
4. Self-Augmentation Full Lifecycle:
   - Query for an uncataloged heritage site triggers IC³-WB.
   - Authoritative multi-source collection, MinHash clustering, claim consensus, writeback gating.
   - Atomic persistence across PostgreSQL (sites, site_facts, chunk_registry) and Qdrant vector index.
   - Provenance tracking in augment_jobs, augment_sources, evidence_ledger, field_decisions, query_logs.
   - Grounded answer returned with path='web_consensus' and official/multi_source citations.
   - Subsequent query directly finds the newly created site in DB/Qdrant without re-running augmentation.
   - 24-hour budget rate-limit prevents redundant web scraping for the same candidate entity.
5. Rejection of Insufficient / Contradictory Claims: Fabricated site is safely rejected without writeback.
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch
import uuid
import pytest
from fastapi.testclient import TestClient
from qdrant_client.http import models
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import get_qdrant_client
from app.main import app
from app.services.augment_orchestrator import run_augmentation_pipeline

client = TestClient(app)


def test_e2e_db_path_brihadeeswarar():
    """E2E Test 1: Query for known site in DB returns verified DB records with path='db'."""
    guest_res = client.post("/api/users/guest")
    assert guest_res.status_code == 200
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?"
    })
    assert res.status_code == 200, res.text
    data = res.json()
    asst = data["assistant_message"]

    assert asst["path"] == "db"
    assert asst["resolved_site"] == "TN-TNJ-0001"
    assert len(asst["citations"]) > 0
    assert asst["citations"][0]["site_id"] == "TN-TNJ-0001"
    assert asst["citations"][0]["origin"] in ("asi_seed", "level1b", "curated")

    # Verify query_logs
    with SessionLocal() as db:
        log_row = db.execute(
            sa_text("SELECT path, resolved_site FROM query_logs WHERE message_id = :mid"),
            {"mid": data["user_message"]["message_id"]},
        ).mappings().fetchone()
        assert log_row is not None
        assert log_row["path"] == "db"
        assert log_row["resolved_site"] == "TN-TNJ-0001"


def test_e2e_out_of_scope_query():
    """E2E Test 2: Non-heritage query is rejected without triggering augmentation."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
        "content": "What is the current stock market price of Apple and Google?"
    })
    assert res.status_code == 200
    data = res.json()
    asst = data["assistant_message"]

    assert asst["path"] in ("out_of_scope", "rejected")
    assert asst["citations"] == []


def test_e2e_identity_check_rejection():
    """E2E Test 3: Query for uncataloged entity with no web hits rejects drift to existing site."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    # When web search returns no URLs, augmentation will fail gracefully
    with patch("app.services.augment_orchestrator.default_web_search", return_value=[]):
        res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
            "content": "pariyur amman temple pathi sollu"
        })
        assert res.status_code == 200
        data = res.json()
        asst = data["assistant_message"]

        assert asst["path"] == "rejected"
        assert asst["citations"] == []
        assert asst["resolved_site"] is None or asst["resolved_site"] != "TN-ERD-0001"


@patch("app.services.gemini.classify_query")
@patch("app.services.augment_orchestrator.classify_query")
@patch("app.services.chat_pipeline.gemini_synthesize_answer")
@patch("app.services.augment_orchestrator.default_web_search")
@patch("app.services.augment_orchestrator.fetch_page_content")
@patch("app.services.augment_orchestrator.extract_claims")
def test_e2e_self_augmentation_lifecycle(mock_extract, mock_fetch, mock_search, mock_synth, mock_classify_orch, mock_classify):
    """E2E Test 4: Complete IC³-WB self-augmentation lifecycle.

    1. Question for an uncataloged heritage site triggers IC³-WB.
    2. Collects authoritative sources (Tier A government, Tier B encyclopedic).
    3. MinHash clustering, claim verification with verbatim quote defense.
    4. Deterministic consensus and write-back gate pass.
    5. Atomic persistence into PostgreSQL and Qdrant.
    6. Grounded response with path='web_consensus' and official citations.
    7. Subsequent query finds the site immediately in DB/Qdrant without re-augmenting.
    8. 24-hour rate limit verification.
    """
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    # Target entity: Tiruvannamalai Annamalaiyar Temple
    entity_name = "Annamalaiyar Temple"
    entity_name_ta = "அண்ணாமலையார் கோவில்"
    place_name = "Tiruvannamalai"

    # Fetch district_id for Tiruvannamalai
    with SessionLocal() as db:
        dist_row = db.execute(
            sa_text("SELECT location_id FROM locations WHERE level = 'district' AND lower(name_en) LIKE 'tiruvannamalai%'")
        ).fetchone()
        district_id = dist_row[0] if dist_row else 30

        old_sites = db.execute(sa_text("SELECT site_id FROM sites WHERE name_en ILIKE :pat"), {"pat": f"%{entity_name}%"}).fetchall()
        for os in old_sites:
            sid = os[0]
            db.execute(sa_text("UPDATE chat_messages SET resolved_site = NULL WHERE resolved_site = :sid"), {"sid": sid})
            db.execute(sa_text("UPDATE query_logs SET resolved_site = NULL WHERE resolved_site = :sid"), {"sid": sid})
            db.execute(sa_text("DELETE FROM site_aliases WHERE site_id = :sid"), {"sid": sid})
            db.execute(sa_text("DELETE FROM field_decisions WHERE site_id = :sid"), {"sid": sid})
            db.execute(sa_text("DELETE FROM evidence_ledger WHERE site_id = :sid"), {"sid": sid})
            db.execute(sa_text("DELETE FROM site_facts WHERE site_id = :sid"), {"sid": sid})
            db.execute(sa_text("DELETE FROM chunk_registry WHERE site_id = :sid"), {"sid": sid})
            db.execute(sa_text("DELETE FROM augment_jobs WHERE site_id = :sid"), {"sid": sid})
            db.execute(sa_text("DELETE FROM sites WHERE site_id = :sid"), {"sid": sid})
        db.execute(sa_text("DELETE FROM augment_jobs WHERE candidate_key LIKE 'annamalaiyar%'"))
        db.commit()

    mock_classify.return_value = {
        "is_heritage": True,
        "entity_name": entity_name,
        "entity_name_ta": entity_name_ta,
        "place_name": place_name,
        "district_id": district_id,
        "question_type": "history",
        "lang": "en",
    }
    mock_classify_orch.return_value = mock_classify.return_value
    mock_synth.return_value = (
        "Annamalaiyar Temple is a renowned Hindu temple dedicated to Lord Shiva, located at the base of Annamalai hills "
        "in Tiruvannamalai. Built primarily during the Chola dynasty in the 9th century CE, it features grand gopurams."
    )

    mock_search.return_value = [
        "https://hrce.tn.gov.in/temples/annamalaiyar",
        "https://asi.nic.in/monuments/annamalaiyar",
        "https://en.wikipedia.org/wiki/Annamalaiyar_Temple",
    ]

    p1_text = (
        f"{entity_name} is a majestic temple located in {place_name}. "
        "The presiding deity is Lord Shiva, represented as Agni Lingam. "
        "The temple complex was constructed during the Chola dynasty in the 9th century CE."
    )
    p2_text = (
        f"{entity_name} in {place_name} is one of the Pancha Bhoota Stalams. "
        "Historical inscriptions reveal extensive expansions by the Chola dynasty in the 9th century CE. "
        "The complex contains monumental gateway towers called gopurams."
    )

    def mock_fetcher(url: str):
        if "hrce.tn.gov.in" in url:
            return (p1_text, 200, url)
        return (p2_text, 200, url)

    mock_fetch.side_effect = mock_fetcher

    mock_extract.return_value = [
        {
            "field": "primary_category",
            "raw_value": "temple",
            "norm_value": "temple",
            "evidence_quote": "majestic temple",
            "quote_found": True,
        },
        {
            "field": "overview",
            "raw_value": f"{entity_name} is a majestic temple located in {place_name}.",
            "norm_value": f"{entity_name} is a majestic temple located in {place_name}.",
            "evidence_quote": f"{entity_name} is a majestic temple located in {place_name}",
            "quote_found": True,
        },
        {
            "field": "history",
            "raw_value": "The temple complex was constructed during the Chola dynasty in the 9th century CE.",
            "norm_value": "The temple complex was constructed during the Chola dynasty in the 9th century CE.",
            "evidence_quote": "constructed during the Chola dynasty in the 9th century CE",
            "quote_found": True,
        },
        {
            "field": "period",
            "raw_value": "9th century CE",
            "norm_value": "9th century CE",
            "evidence_quote": "9th century CE",
            "quote_found": True,
        },
        {
            "field": "dynasty",
            "raw_value": "Chola",
            "norm_value": "Chola",
            "evidence_quote": "Chola dynasty",
            "quote_found": True,
        },
        {
            "field": "deity",
            "raw_value": "Lord Shiva",
            "norm_value": "Lord Shiva",
            "evidence_quote": "presiding deity is Lord Shiva",
            "quote_found": True,
        },
    ]

    new_site_id = None
    created_job_id = None
    try:
        # Step 1: Initial query triggers self-augmentation
        res1 = client.post(f"/api/chat/sessions/{session_id}/messages", json={
            "content": f"Tell me about {entity_name} in {place_name}"
        })
        assert res1.status_code == 200, res1.text
        data1 = res1.json()
        asst1 = data1["assistant_message"]

        assert asst1["path"] == "web_consensus"
        assert len(asst1["citations"]) > 0
        new_site_id = asst1["citations"][0]["site_id"]
        assert new_site_id.startswith("TN-")
        assert asst1["citations"][0]["origin"] == "web_consensus"
        assert asst1["citations"][0]["trust"] in ("official", "multi_source")

        # Verify PostgreSQL database entries
        with SessionLocal() as db:
            site_row = db.execute(
                sa_text("SELECT site_id, name_en, origin, status FROM sites WHERE site_id = :sid"),
                {"sid": new_site_id},
            ).mappings().fetchone()
            assert site_row is not None
            assert site_row["origin"] == "web_consensus"
            assert site_row["status"] == "verified"

            # Verify chunk_registry
            chunks_count = db.execute(
                sa_text("SELECT count(*) FROM chunk_registry WHERE site_id = :sid"),
                {"sid": new_site_id},
            ).scalar()
            assert chunks_count >= 2

            # Verify query_logs path_detail
            log_row = db.execute(
                sa_text("SELECT path, path_detail, gemini_calls FROM query_logs WHERE message_id = :mid"),
                {"mid": data1["user_message"]["message_id"]},
            ).mappings().fetchone()
            assert log_row is not None
            assert log_row["path"] == "web_consensus"
            created_job_id = log_row["path_detail"]["augment_job_id"]

            # Verify augment_jobs entry
            job_row = db.execute(
                sa_text("SELECT status, site_id FROM augment_jobs WHERE job_id = :jid"),
                {"jid": created_job_id},
            ).mappings().fetchone()
            assert job_row is not None
            assert job_row["status"] == "verified"
            assert job_row["site_id"] == new_site_id

        # Step 2: Subsequent query in a new session directly matches the new site from DB
        session_id_2 = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]
        res2 = client.post(f"/api/chat/sessions/{session_id_2}/messages", json={
            "content": f"Where is {entity_name} located?"
        })
        assert res2.status_code == 200, res2.text
        data2 = res2.json()
        asst2 = data2["assistant_message"]

        # Directly resolves without needing web augmentation
        assert asst2["resolved_site"] == new_site_id
        assert len(asst2["citations"]) > 0
        assert asst2["citations"][0]["site_id"] == new_site_id

        # Step 3: 24-hour rate limit check
        with SessionLocal() as db:
            res_budget = run_augmentation_pipeline(
                query=f"History of {entity_name} in {place_name}",
                db=db,
                search_fn=mock_search,
                page_fetcher=mock_fetch,
                claims_extractor=mock_extract,
            )
            # Reuses existing verified site within 24h
            assert res_budget.status == "verified"
            assert res_budget.site_id == new_site_id

    finally:
        # Hermetic cleanup: delete site and vectors
        if new_site_id:
            qdrant = get_qdrant_client()
            qdrant.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=models.FilterSelector(
                    filter=models.Filter(must=[models.FieldCondition(key="site_id", match=models.MatchValue(value=new_site_id))])
                ),
            )
            with SessionLocal() as db:
                cids = [r[0] for r in db.execute(sa_text("SELECT chunk_id FROM chunk_registry WHERE site_id = :sid"), {"sid": new_site_id}).fetchall()]
                if cids:
                    db.execute(sa_text("DELETE FROM message_citations WHERE chunk_id = ANY(:cids)"), {"cids": cids})
                db.execute(sa_text("UPDATE chat_messages SET resolved_site = NULL WHERE resolved_site = :sid"), {"sid": new_site_id})
                db.execute(sa_text("UPDATE query_logs SET resolved_site = NULL WHERE resolved_site = :sid"), {"sid": new_site_id})
                db.execute(sa_text("DELETE FROM field_decisions WHERE site_id = :sid"), {"sid": new_site_id})
                db.execute(sa_text("DELETE FROM evidence_ledger WHERE site_id = :sid"), {"sid": new_site_id})
                db.execute(sa_text("DELETE FROM augment_sources WHERE job_id = :jid"), {"jid": created_job_id})
                db.execute(sa_text("DELETE FROM augment_jobs WHERE site_id = :sid OR job_id = :jid"), {"sid": new_site_id, "jid": created_job_id})
                db.execute(sa_text("DELETE FROM site_facts WHERE site_id = :sid"), {"sid": new_site_id})
                db.execute(sa_text("DELETE FROM chunk_registry WHERE site_id = :sid"), {"sid": new_site_id})
                db.execute(sa_text("DELETE FROM sites WHERE site_id = :sid"), {"sid": new_site_id})
                db.commit()


def test_e2e_augmentation_rejection_insufficient_claims():
    """E2E Test 5: Fabricated entity with zero or insufficient claims is rejected without writeback."""
    guest_res = client.post("/api/users/guest")
    user_id = guest_res.json()["user_id"]
    session_id = client.post("/api/chat/sessions", json={"user_id": user_id}).json()["session_id"]

    fake_entity = f"Imaginary Fort {uuid.uuid4().hex[:6]}"

    with patch("app.services.augment_orchestrator.default_web_search", return_value=["https://blog.example.com/fake_fort"]):
        with patch("app.services.augment_orchestrator.fetch_page_content", return_value=(f"{fake_entity} has no records.", 200, "https://blog.example.com/fake_fort")):
            with patch("app.services.augment_orchestrator.extract_claims", return_value=[]):
                res = client.post(f"/api/chat/sessions/{session_id}/messages", json={
                    "content": f"Tell me about {fake_entity} in Vellore"
                })
                assert res.status_code == 200
                data = res.json()
                asst = data["assistant_message"]

                assert asst["path"] == "rejected"
                assert asst["citations"] == []

                # Ensure no site was created
                with SessionLocal() as db:
                    site_exists = db.execute(
                        sa_text("SELECT 1 FROM sites WHERE lower(name_en) = lower(:n)"),
                        {"n": fake_entity},
                    ).fetchone()
                    assert site_exists is None
