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
from app.services.spec_config import GEMINI_CLASSIFY_TIMEOUT_SEC

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
    timeout: Optional[float] = None,
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
    req_timeout = timeout or settings.GEMINI_TIMEOUT_SEC

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=req_timeout) as client:
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
def _rule_based_classify(clean_text: str, db: Optional[Session] = None) -> Dict[str, Any]:
    """Deterministic rule-based fallback when Gemini classification fails or times out."""
    from app.services.chat_pipeline import (
        classify_question_type as rule_classify_question_type,
        detect_language as rule_detect_language,
        extract_entity_name as rule_extract_entity_name,
    )
    from app.services.place_resolution import resolve_place

    lang = rule_detect_language(clean_text)
    q_type = rule_classify_question_type(clean_text)

    # Check non-heritage patterns
    non_heritage_patterns = [
        r"what\s+is\s+(?:ur|your)\s+name",
        r"who\s+are\s+you",
        r"tell\s+me\s+a\s+joke",
        r"how\s+are\s+you",
        r"weather", r"cricket", r"bitcoin", r"crypto", r"election"
    ]
    if any(re.search(pat, clean_text, re.IGNORECASE) for pat in non_heritage_patterns):
        q_type = "out_of_scope"

    is_heritage = (q_type != "out_of_scope")
    entity = rule_extract_entity_name(clean_text) if is_heritage else None

    # Resolve place
    res_place = resolve_place(clean_text, None, db=db)
    place_name = res_place.matched_term
    district_id = res_place.district_id

    # Determine entity_type: specific_site | deity_or_generic_name | region_or_list | concept
    entity_type = "specific_site"
    generic_deity_names = ["ponniamman", "mariamman", "ayyanar", "kaliamman", "vinayagar", "murugan", "muniswaran", "karuppasamy", "masani amman"]
    if entity and any(d in entity.lower() for d in generic_deity_names) and not place_name:
        entity_type = "deity_or_generic_name"
    elif not entity:
        entity_type = "concept"

    # Determine asked_field
    asked_field = "overview"
    if q_type == "history":
        asked_field = "builder" if any(w in clean_text.lower() for w in ["who built", "builder", "kattiyavar"]) else "built_year"
    elif q_type == "location":
        asked_field = "location"
    elif q_type == "timings_entry":
        asked_field = "timings"
    elif q_type == "festival":
        asked_field = "festivals"
    elif q_type == "architecture":
        asked_field = "architecture_style"

    final_q_type = q_type or ("out_of_scope" if not is_heritage else "general_heritage")

    return {
        "is_heritage": is_heritage,
        "entity_name": entity,
        "entity_name_ta": None,
        "place_name": place_name,
        "district_id": district_id,
        "entity_type": entity_type,
        "asked_field": asked_field,
        "question_type": final_q_type,
        "lang": lang,
        "fallback_used": True,
    }


def classify_query(
    text: str,
    db: Optional[Session] = None,
    timeout_sec: float = GEMINI_CLASSIFY_TIMEOUT_SEC,
) -> Dict[str, Any]:
    """Classify user query into heritage intent, entity, place, district, question type, and language.

    Rule 1.1: Returns dict with keys:
      is_heritage: bool
      lang: str
      question_type: str (never empty)
      entity_name: Optional[str]
      entity_name_ta: Optional[str]
      place_name: Optional[str]
      entity_type: str ('specific_site' | 'deity_or_generic_name' | 'region_or_list' | 'concept')
      asked_field: Optional[str]
      district_id: Optional[int]
      fallback_used: bool
    """
    clean_text = text.strip()

    # If GEMINI_API_KEY is not configured, fallback immediately
    if not settings.GEMINI_API_KEY:
        return _rule_based_classify(clean_text, db=db)

    system_inst = (
        "You are an expert NLP classifier for Tamil Nadu heritage queries.\n"
        "Analyze the user's question and output a single JSON object with the following fields:\n"
        "- is_heritage (boolean): whether the question is about Tamil Nadu heritage, monuments, temples, history, culture, etc. "
        "Set to false for non-heritage queries like greetings ('hi', 'hello'), general bot questions ('what is ur name', 'who are you'), chit-chat, weather, sports, politics, math, or coding.\n"
        "- entity_name (string or null): specific monument/temple/fort in English (e.g. 'Pariyur Kondathu Kaliamman Temple')\n"
        "- entity_name_ta (string or null): entity name in Tamil script if available\n"
        "- place_name (string or null): town, village, or district mentioned (e.g. 'Pollachi', 'Erode')\n"
        "- entity_type (string): one of ['specific_site', 'deity_or_generic_name', 'region_or_list', 'concept'].\n"
        "  Few-shot rules:\n"
        "  * 'ponniamman temple' without a place -> deity_or_generic_name\n"
        "  * 'mariamman kovil' without a place -> deity_or_generic_name\n"
        "  * 'ayyanar temple' without a place -> deity_or_generic_name\n"
        "  * 'masani amman temple' without a place -> deity_or_generic_name\n"
        "  * 'masani amman temple at pollachi' -> specific_site\n"
        "  * 'madurai meenakshi' -> specific_site\n"
        "  * 'temples in Madurai' -> region_or_list\n"
        "  * 'dravidian architecture' -> concept\n"
        "- asked_field (string or null): attribute asked about, e.g. 'built_year', 'builder', 'location', 'timings', 'festivals', 'overview', 'architecture'\n"
        "- question_type (string): one of [history, architecture, location, how_to_reach, timings_entry, festival, recommendation, comparison, out_of_scope, general_heritage]. MUST NOT be empty.\n"
        "- lang (string): 'ta' (Tamil script), 'kn' (Kannada script), 'ta-Latn' (Tanglish/Tamil transliterated in Latin script), or 'en' (English)\n"
        "Return ONLY the raw JSON object."
    )

    try:
        call_res = _call_gemini_api(
            clean_text,
            system_instruction=system_inst,
            json_response=True,
            timeout=timeout_sec,
        )
        raw_json = json.loads(call_res["text"])

        is_heritage = bool(raw_json.get("is_heritage", True))
        q_type = raw_json.get("question_type") or ("out_of_scope" if not is_heritage else "general_heritage")
        if not is_heritage:
            q_type = "out_of_scope"

        entity_type = raw_json.get("entity_type") or ("concept" if not raw_json.get("entity_name") else "specific_site")
        if entity_type not in {"specific_site", "deity_or_generic_name", "region_or_list", "concept"}:
            entity_type = "specific_site"

        entity_name = raw_json.get("entity_name")
        place_name = raw_json.get("place_name")
        district_id = None

        if db and (place_name or clean_text):
            from app.services.place_resolution import resolve_place
            res_place = resolve_place(clean_text, place_name, db=db)
            district_id = res_place.district_id
            if res_place.matched_term and not place_name:
                place_name = res_place.matched_term

        from app.services.chat_pipeline import detect_language as rule_detect_language
        return {
            "is_heritage": is_heritage,
            "entity_name": entity_name,
            "entity_name_ta": raw_json.get("entity_name_ta"),
            "place_name": place_name,
            "district_id": district_id,
            "entity_type": entity_type,
            "asked_field": raw_json.get("asked_field") or "overview",
            "question_type": q_type,
            "lang": raw_json.get("lang") or rule_detect_language(clean_text),
            "fallback_used": False,
        }
    except Exception as e:
        logger.warning(f"Gemini query classification failed or timed out ({e}), falling back to rule-based classifier.")
        return _rule_based_classify(clean_text, db=db)


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
        f"Target fields to extract if mentioned:\n"
        "- name_en: English name of the heritage site\n"
        "- name_ta: Tamil name of the heritage site\n"
        "- primary_category: Site category (e.g. 'temple', 'fort', 'palace', 'monument', 'museum', etc.)\n"
        "- district_id: District name where it is situated (e.g. 'Erode', 'Madurai', 'Thanjavur')\n"
        "- deity: Presiding deity or deity worshipped (if a temple/religious site)\n"
        "- builder: King, dynasty patron, or builder who erected/patronized the monument\n"
        "- built_year: Year or century of establishment\n"
        "- period: Historical period\n"
        "- dynasty: Ruling dynasty (e.g. 'Chola', 'Pandya', 'Chera', 'Nayaka', 'Pallava')\n"
        "- overview: 1-2 sentence high-level summary of the site\n"
        "- history: Historical background, legends, or origins of the site\n\n"
        f"Page Text:\n\"\"\"\n{page_text[:12000]}\n\"\"\"\n\n"
        "Instructions:\n"
        "Extract factual claims for the specified entity from the page text. "
        "For each fact, output a JSON array of objects with keys:\n"
        "- field: target field name (use exact field name from above)\n"
        "- raw_value: the extracted fact value as mentioned in text\n"
        "- norm_value: standardized representation\n"
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
            field = str(claim.get("field", "")).strip().lower()
            if field in ("district", "district_name"):
                field = "district_id"
            elif field in ("category", "type", "monument_type"):
                field = "primary_category"
            raw_val = str(claim.get("raw_value", "")).strip()
            norm_val = str(claim.get("norm_value", raw_val)).strip()
            quote = str(claim.get("evidence_quote", "")).strip()

            if not field or not raw_val or not quote:
                continue

            # Strict Hallucination Defense: quote must exist verbatim or normalized in page_text
            quote_clean = re.sub(r"\s+", " ", quote).strip().lower()
            page_clean = re.sub(r"\s+", " ", page_text).strip().lower()
            if quote in page_text or quote_clean in page_clean:
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
