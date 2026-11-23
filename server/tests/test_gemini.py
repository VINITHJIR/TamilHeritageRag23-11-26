"""Unit tests for Gemini service (server/app/services/gemini.py)."""

import json
from unittest.mock import MagicMock, patch
import httpx
import pytest
from app.services.gemini import (
    GeminiServiceError,
    _call_gemini_api,
    classify_query,
    extract_claims,
    synthesize_answer,
)


def test_classify_query_fallback_when_no_api_key():
    """When GEMINI_API_KEY is not set, classify_query falls back to deterministic rule-based logic."""
    with patch("app.services.gemini.settings.GEMINI_API_KEY", None):
        res = classify_query("தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?")
        assert res["is_heritage"] is True
        assert res["lang"] == "ta"
        assert res["question_type"] == "history"
        assert res["entity_name"] is not None


def test_classify_query_with_gemini_mock():
    """Test Gemini classification parsing with mock API response."""
    mock_payload = {
        "is_heritage": True,
        "entity_name": "Pariyur Kondathu Kaliamman Temple",
        "entity_name_ta": "பரியூர் கொண்டத்து காளியம்மன் கோவில்",
        "place_name": "Gobichettipalayam",
        "question_type": "history",
        "lang": "en",
    }
    mock_api_return = {
        "text": json.dumps(mock_payload),
        "total_tokens": 120,
        "prompt_tokens": 80,
        "candidate_tokens": 40,
        "latency_ms": 350,
    }

    with patch("app.services.gemini.settings.GEMINI_API_KEY", "mock-key"), \
         patch("app.services.gemini._call_gemini_api", return_value=mock_api_return):
        res = classify_query("Tell me about Pariyur Amman Temple")
        assert res["is_heritage"] is True
        assert res["entity_name"] == "Pariyur Kondathu Kaliamman Temple"
        assert res["place_name"] == "Gobichettipalayam"
        assert res["question_type"] == "history"
        assert res["lang"] == "en"


def test_extract_claims_hallucination_defense():
    """Verify exact verbatim quote verification: claims without verbatim quote in text are dropped."""
    page_text = (
        "Pariyur Kondathu Kaliamman Temple is a famous Hindu temple located in Gobichettipalayam, Tamil Nadu. "
        "The current stone structure was built during the Nayaka period in the 17th century. "
        "The presiding deity is Goddess Kondathu Kaliamman."
    )

    mock_gemini_claims = [
        # Claim 1: Valid exact quote
        {
            "field": "period",
            "raw_value": "17th century",
            "norm_value": "17th century CE",
            "evidence_quote": "built during the Nayaka period in the 17th century",
        },
        # Claim 2: Hallucinated quote not in page text
        {
            "field": "builder",
            "raw_value": "Raja Raja Chola",
            "norm_value": "Raja Raja Chola I",
            "evidence_quote": "built by King Raja Raja Chola in 1010 CE",
        },
    ]

    mock_api_return = {
        "text": json.dumps(mock_gemini_claims),
        "total_tokens": 200,
        "prompt_tokens": 150,
        "candidate_tokens": 50,
        "latency_ms": 400,
    }

    with patch("app.services.gemini.settings.GEMINI_API_KEY", "mock-key"), \
         patch("app.services.gemini._call_gemini_api", return_value=mock_api_return):
        extracted = extract_claims(
            page_text=page_text,
            entity_name="Pariyur Kondathu Kaliamman Temple",
        )

        assert len(extracted) == 1
        assert extracted[0]["field"] == "period"
        assert extracted[0]["norm_value"] == "17th century CE"
        assert extracted[0]["quote_found"] is True


def test_synthesize_answer_grounding():
    """Verify grounded answer synthesis with facts list."""
    facts = [
        {"field": "name_en", "value": "Pariyur Kondathu Kaliamman Temple"},
        {"field": "deity", "value": "Kondathu Kaliamman"},
        {"field": "district", "value": "Erode"},
    ]

    # Without API key, uses deterministic fact listing template
    with patch("app.services.gemini.settings.GEMINI_API_KEY", None):
        res = synthesize_answer("Where is Pariyur temple?", facts, lang="en")
        assert "Pariyur Kondathu Kaliamman Temple" in res
        assert "Erode" in res


def test_retry_on_rate_limit():
    """Verify _call_gemini_api retries on HTTP 429/503 before succeeding."""
    mock_resp_429 = MagicMock(spec=httpx.Response)
    mock_resp_429.status_code = 429

    mock_resp_200 = MagicMock(spec=httpx.Response)
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Success after retry"}]}}],
        "usageMetadata": {"totalTokenCount": 25, "promptTokenCount": 15, "candidatesTokenCount": 10},
    }

    with patch("app.services.gemini.settings.GEMINI_API_KEY", "test-key"), \
         patch("httpx.Client.post", side_effect=[mock_resp_429, mock_resp_200]), \
         patch("time.sleep"):  # Mock sleep so tests don't wait
        result = _call_gemini_api("Hello")
        assert result["text"] == "Success after retry"
        assert result["total_tokens"] == 25
