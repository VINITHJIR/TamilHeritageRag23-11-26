"""Google Gemini service for TamilHeritage-GPT.

Provides LLM classification, claim extraction, and grounded answer synthesis.
Follows the core design principle: Gemini only READS and WRITES text; all consensus,
filtering, and writeback decisions are deterministic Python code.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.core.config import settings

logger = logging.getLogger(__name__)

GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiServiceError(Exception):
    """Raised when Gemini API request fails or retries are exhausted."""
    pass


def _call_gemini_api(
    prompt: str,
    system_instruction: Optional[str] = None,
    json_response: bool = False,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Call the Google Gemini REST API with exponential backoff on 429/503.

    Returns dict containing 'text', 'total_tokens', 'prompt_tokens', 'candidate_tokens', 'latency_ms'.
    """
    key = api_key or settings.GEMINI_API_KEY
    if not key:
        raise GeminiServiceError("GEMINI_API_KEY is not configured in settings or environment.")

    model_name = model or settings.GEMINI_MODEL
    url = GEMINI_API_URL_TEMPLATE.format(model=model_name)

    body: Dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,  # Low temperature for factual extraction / classification
        },
    }

    if system_instruction:
        body["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    if json_response:
        body["generationConfig"]["responseMimeType"] = "application/json"

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    }

    backoff = 1.0
    start_time = time.time()

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=settings.GEMINI_TIMEOUT_SEC) as client:
                res = client.post(url, json=body, headers=headers)

                if res.status_code in (429, 503):
                    if attempt < max_retries:
                        logger.warning(
                            f"Gemini API returned {res.status_code} (attempt {attempt}/{max_retries}). "
                            f"Retrying in {backoff:.1f}s..."
                        )
                        time.sleep(backoff)
                        backoff *= 2.0
                        continue
                    res.raise_for_status()

                res.raise_for_status()
                data = res.json()
                latency_ms = int((time.time() - start_time) * 1000)

                # Extract text and usage tokens
                candidates = data.get("candidates", [])
                if not candidates:
                    raise GeminiServiceError("Gemini returned empty candidates.")

                candidate = candidates[0]
                text_content = ""
                content_parts = candidate.get("content", {}).get("parts", [])
                for part in content_parts:
                    if "text" in part:
                        text_content += part["text"]

                usage = data.get("usageMetadata", {})
                total_tokens = usage.get("totalTokenCount", 0)
                prompt_tokens = usage.get("promptTokenCount", 0)
                candidate_tokens = usage.get("candidatesTokenCount", 0)

                logger.info(
                    f"Gemini API call succeeded [{model_name}]: {total_tokens} tokens, latency: {latency_ms}ms"
                )

                return {
                    "text": text_content,
                    "total_tokens": total_tokens,
                    "prompt_tokens": prompt_tokens,
                    "candidate_tokens": candidate_tokens,
                    "latency_ms": latency_ms,
                }

        except httpx.HTTPStatusError as e:
            if attempt == max_retries:
                logger.error(f"Gemini API failed with status {e.response.status_code}: {e.response.text}")
                raise GeminiServiceError(f"Gemini API HTTP {e.response.status_code}") from e
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"Gemini API call failed: {e}", exc_info=True)
                raise GeminiServiceError(f"Gemini API error: {e}") from e

        time.sleep(backoff)
        backoff *= 2.0

    raise GeminiServiceError("Exhausted retries calling Gemini API.")


# -----------------------------------------------------------------------------
# 1. Query Classification
# -----------------------------------------------------------------------------
def classify_query(text: str, db: Optional[Session] = None) -> Dict[str, Any]:
    """Classify user query into heritage intent, entity, place, district, question type, and language.

    Returns dict with keys:
      is_heritage: bool
      entity_name: Optional[str]
      entity_name_ta: Optional[str]
      place_name: Optional[str]
      district_id: Optional[int]
      question_type: str
      lang: str
    """
    from app.services.chat_pipeline import (
        classify_question_type as rule_classify_question_type,
        detect_language as rule_detect_language,
        extract_entity_name as rule_extract_entity_name,
    )

    clean_text = text.strip()

    # If GEMINI_API_KEY is not configured or in unit test mode without key, fallback to rule-based
    if not settings.GEMINI_API_KEY:
        lang = rule_detect_language(clean_text)
        q_type = rule_classify_question_type(clean_text)
        entity = rule_extract_entity_name(clean_text)
        is_heritage = q_type != "out_of_scope"

        district_id = None
        if db and entity:
            dist_row = db.execute(
                sa_text("SELECT location_id FROM locations WHERE level = 'district' AND lower(name_en) = lower(:p)"),
                {"p": entity},
            ).fetchone()
            if dist_row:
                district_id = dist_row[0]

        return {
            "is_heritage": is_heritage,
            "entity_name": entity,
            "entity_name_ta": None,
            "place_name": None,
            "district_id": district_id,
            "question_type": q_type,
            "lang": lang,
        }

    system_inst = (
        "You are an expert NLP classifier for Tamil Nadu heritage queries. "
        "Analyze the user's question and output a single JSON object with the following fields:\n"
        "- is_heritage (boolean): whether the question is about Tamil Nadu heritage, monuments, temples, history, etc.\n"
        "- entity_name (string or null): the specific monument/temple/fort in English (e.g. 'Pariyur Kondathu Kaliamman Temple')\n"
        "- entity_name_ta (string or null): entity name in Tamil script if available\n"
        "- place_name (string or null): town, village, or district mentioned\n"
        "- question_type (string): one of [history, architecture, location, how_to_reach, timings_entry, festival, recommendation, comparison, out_of_scope, general_heritage]\n"
        "- lang (string): 'ta' (Tamil script), 'kn' (Kannada script), 'ta-Latn' (Tanglish/Tamil transliterated in Latin script), or 'en' (English)\n"
        "Return ONLY the raw JSON object."
    )

    try:
        call_res = _call_gemini_api(clean_text, system_instruction=system_inst, json_response=True)
        raw_json = json.loads(call_res["text"])

        entity_name = raw_json.get("entity_name")
        place_name = raw_json.get("place_name")
        district_id = None

        if db and place_name:
            dist_row = db.execute(
                sa_text("""
                    SELECT location_id FROM locations
                    WHERE level = 'district'
                      AND (lower(name_en) = lower(:p) OR lower(name_ta) = lower(:p))
                """),
                {"p": place_name.strip()},
            ).fetchone()
            if dist_row:
                district_id = dist_row[0]

        return {
            "is_heritage": bool(raw_json.get("is_heritage", True)),
            "entity_name": entity_name,
            "entity_name_ta": raw_json.get("entity_name_ta"),
            "place_name": place_name,
            "district_id": district_id,
            "question_type": raw_json.get("question_type", "general_heritage"),
            "lang": raw_json.get("lang", rule_detect_language(clean_text)),
        }
    except Exception as e:
        logger.warning(f"Gemini query classification failed ({e}), falling back to rule-based classifier.")
        lang = rule_detect_language(clean_text)
        q_type = rule_classify_question_type(clean_text)
        entity = rule_extract_entity_name(clean_text)
        return {
            "is_heritage": q_type != "out_of_scope",
            "entity_name": entity,
            "entity_name_ta": None,
            "place_name": None,
            "district_id": None,
            "question_type": q_type,
            "lang": lang,
        }


# -----------------------------------------------------------------------------
# 2. Claim Extraction with Hallucination Defense (Verbatim Quote Verification)
# -----------------------------------------------------------------------------
def extract_claims(
    page_text: str,
    entity_name: str,
    place_name: Optional[str] = None,
    target_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Extract factual claims from page_text for the target entity.

    Enforces strict hallucination defense:
    evidence_quote MUST be an exact verbatim substring from page_text.
    Claims without an exact quote match in page_text are discarded.
    """
    if not page_text or not page_text.strip():
        return []

    fields_to_extract = target_fields or [
        "name_en", "name_ta", "primary_category", "district", "builder",
        "built_year", "period", "dynasty", "deity", "festival", "overview", "history"
    ]

    prompt = (
        f"Entity: {entity_name}\n"
        f"Place: {place_name or 'Tamil Nadu'}\n"
        f"Target fields: {', '.join(fields_to_extract)}\n\n"
        f"Page Text:\n\"\"\"\n{page_text[:12000]}\n\"\"\"\n\n"
        "Instructions:\n"
        "Extract factual claims for the specified entity from the page text. "
        "For each fact, output a JSON array of objects with keys:\n"
        "- field: name of the field (e.g. 'builder', 'built_year', 'deity', 'festival', 'history', etc.)\n"
        "- raw_value: the extracted fact value as mentioned in text\n"
        "- norm_value: standardized/normalized representation (e.g. 'Raja Raja Chola I', '1010 CE')\n"
        "- evidence_quote: CRITICAL REQUIREMENT: This MUST be an EXACT, VERBATIM substring copied directly from the page text above containing the proof.\n"
        "Return ONLY a JSON array."
    )

    system_inst = (
        "You are a factual claim extractor for historical heritage texts. "
        "Never hallucinate or extrapolate facts. "
        "Every claim MUST include an exact verbatim evidence_quote directly found in the text."
    )

    if not settings.GEMINI_API_KEY:
        # Fallback/mock mode when no API key configured: return empty list or regex extract
        return []

    try:
        call_res = _call_gemini_api(prompt, system_instruction=system_inst, json_response=True)
        raw_claims = json.loads(call_res["text"])
        if not isinstance(raw_claims, list):
            return []

        verified_claims: List[Dict[str, Any]] = []
        for claim in raw_claims:
            field = str(claim.get("field", "")).strip()
            raw_val = str(claim.get("raw_value", "")).strip()
            norm_val = str(claim.get("norm_value", raw_val)).strip()
            quote = str(claim.get("evidence_quote", "")).strip()

            if not field or not raw_val or not quote:
                continue

            # Strict Hallucination Defense: quote must exist verbatim in page_text
            if quote in page_text:
                verified_claims.append({
                    "field": field,
                    "raw_value": raw_val,
                    "norm_value": norm_val,
                    "evidence_quote": quote,
                    "quote_found": True,
                })
            else:
                logger.warning(
                    f"Hallucination defense rejected claim for field '{field}': "
                    f"quote '{quote[:60]}...' was not found verbatim in page text."
                )

        return verified_claims

    except Exception as e:
        logger.error(f"Error in Gemini claim extraction: {e}", exc_info=True)
        return []


# -----------------------------------------------------------------------------
# 3. Grounded Answer Synthesis
# -----------------------------------------------------------------------------
def synthesize_answer(
    query: str,
    facts: List[Dict[str, Any]],
    lang: str = "en",
) -> str:
    """Synthesize a grounded answer using only verified facts from the consensus ledger.

    Grounding rule: Refuse to invent details not backed by the provided facts.
    """
    if not facts:
        if lang == "ta":
            return "மன்னிக்கவும், எங்கள் பதிவுகளில் நம்பகமான தகவல்கள் எதுவும் கிடைக்கவில்லை."
        elif lang == "ta-Latn":
            return "Mannikkavum, enga pathivukalil nambakamaana thagavalgal kidaikkavillai."
        elif lang == "kn":
            return "ಕ್ಷಮಿಸಿ, ನಮ್ಮ ದಾಖಲೆಗಳಲ್ಲಿ ಯಾವುದೇ ವಿಶ್ವಾಸಾರ್ಹ ಮಾಹಿತಿ ಕಂಡುಬಂದಿಲ್ಲ."
        return "I could not find reliable heritage records matching your query."

    facts_summary = "\n".join(
        f"- {f.get('field')}: {f.get('value')} (Source: {f.get('source_url', 'Consensus records')})"
        for f in facts
    )

    prompt = (
        f"User Query: {query}\n"
        f"Target Language: {lang}\n\n"
        f"Verified Facts:\n{facts_summary}\n\n"
        "Instructions:\n"
        "Synthesize a clear, helpful, conversational answer to the user query based ONLY on the verified facts above. "
        "Strictly adhere to the facts provided. Do NOT hallucinate or extrapolate unverified historical details. "
        "Respond in the requested language (Tamil for 'ta', Tanglish for 'ta-Latn', Kannada for 'kn', English for 'en')."
    )

    system_inst = (
        "You are TamilHeritage-GPT, a conversational assistant specialized in Tamil Nadu heritage. "
        "You are strictly grounded: you only answer using the verified facts provided."
    )

    if not settings.GEMINI_API_KEY:
        # Fallback template-based synthesis
        header = "From verified consensus records:\n\n"
        if lang == "ta":
            header = "எங்கள் சரிபார்க்கப்பட்ட பதிவுகளின்படி:\n\n"
        elif lang == "ta-Latn":
            header = "Engal saripaarkkapatta pathivugalin padi:\n\n"
        elif lang == "kn":
            header = "ನಮ್ಮ ಪರಿಶೀಲಿಸಿದ ದಾಖಲೆಗಳ ಪ್ರಕಾರ:\n\n"

        body = "\n".join(f"• {f.get('field', '').replace('_', ' ').title()}: {f.get('value')}" for f in facts)
        return f"{header}{body}"

    try:
        call_res = _call_gemini_api(prompt, system_instruction=system_inst, json_response=False)
        return call_res["text"].strip()
    except Exception as e:
        logger.error(f"Error in Gemini answer synthesis: {e}", exc_info=True)
        # Fallback to direct fact listing
        return "\n".join(f"{f.get('field')}: {f.get('value')}" for f in facts)
