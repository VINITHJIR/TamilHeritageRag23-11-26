"""Chat pipeline service for TamilHeritage-GPT.

Implements the multi-step retrieval-augmented conversational pipeline:
1. Language detection (Unicode script for Tamil/Kannada, Tanglish markers for ta-Latn, fallback English).
2. Question type classification (10 controlled codes, fallback general_heritage).
3. Entity linking / Site detection via pg_trgm similarity on site_aliases + district resolution.
4. Semantic vector search (bge-m3 dense vectors in Qdrant) with language-preference reranking.
5. Entity Identity Check (similarity >= 0.55 & district match) preventing entity drift.
6. Extractive answer generation with template prefixing.
7. Score-based rejection threshold (top_score < 0.45 or identity mismatch -> polite refusal, path='rejected').
8. Database persistence across chat_messages, message_citations, query_logs, and chat_sessions.
9. Request idempotency using client idempotency_key or recent duplicate guard.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid
from qdrant_client.http import models
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.qdrant import get_qdrant_client
from app.schemas.chat import (
    AssistantMessagePayload,
    ChatMessageResponse,
    CitationItem,
    UserMessagePayload,
)
from app.services.augment_orchestrator import run_augmentation_pipeline
from app.services.embeddings import get_embedding_service
from app.services.gemini import synthesize_answer as gemini_synthesize_answer

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# In-memory Idempotency Cache: (session_id_str, idempotency_key) -> ChatMessageResponse
# -----------------------------------------------------------------------------
_IDEMPOTENCY_CACHE: Dict[Tuple[str, str], ChatMessageResponse] = {}

# -----------------------------------------------------------------------------
# 1. Language Detection & Tanglish Extension
# -----------------------------------------------------------------------------
TAMIL_SCRIPT_RE = re.compile(r"[\u0B80-\u0BFF]")
KANNADA_SCRIPT_RE = re.compile(r"[\u0C80-\u0CFF]")

TANGLISH_MARKERS = {
    "vanakkam", "kovil", "koyil", "kovilla", "koyilla", "kovilku", "koviloda", "kovil-la", "kovil-ku", "kovil-oda",
    "templela", "temple-la", "templeoda", "temple-oda", "templeku", "temple-ku",
    "enna", "yaaru", "yaar", "yaarukku", "enga", "engae", "engirukku", "eppadi", "epdi",
    "nadakkum", "irukku", "iruku", "irukkum", "kattiyavar", "kattinaar", "kattinaanga", "kattina",
    "kattunathu", "kattiyadhu", "theekundam", "thiruvizha", "thirunal", "bakthargal", "nerthikadan",
    "seluthuvaanga", "samikku", "paththi", "pathi", "solunga", "sollunga", "sollu", "solla",
    "vandhuchu", "vanthuchu", "eppo", "eppothu", "saami", "ooru", "oorla", "periya", "chinna",
    "nalla", "paarka", "parkalam", "ponum", "vazhi", "mudiyum", "vandhu", "panna", "pannuvanga",
    "theriyuma", "kaattunga", "ungalukku", "theriyum", "varalaaru", "edam"
}

TANGLISH_SUFFIX_RE = re.compile(r"^[a-z]+(-la|-le|-ku|-kku|-oda|-lerundhu)$", re.IGNORECASE)


def detect_language(text: str) -> str:
    """Detect language from query string.

    Returns: 'ta' (Tamil script), 'kn' (Kannada script), 'ta-Latn' (Tanglish), or 'en' (English).
    """
    if TAMIL_SCRIPT_RE.search(text):
        return "ta"
    if KANNADA_SCRIPT_RE.search(text):
        return "kn"

    text_lower = text.lower()
    # Check common Tanglish multi-word phrases
    if any(phrase in text_lower for phrase in (
        "pathi sollu", "paththi sollu", "pathi solla", "paththi solla",
        "enga irukku", "engae irukku", "enna festival", "kattiyavar yaar"
    )):
        return "ta-Latn"

    tokens = [t.lower().strip(",.?!;:'\"-") for t in text.split()]
    for token in tokens:
        if token in TANGLISH_MARKERS:
            return "ta-Latn"
        if "-" in token and TANGLISH_SUFFIX_RE.match(token):
            return "ta-Latn"
        if any(token.endswith(sfx) for sfx in ("-la", "-ku", "-oda", "-le", "-kku", "-lerundhu")):
            return "ta-Latn"
        if token.endswith("la") and len(token) > 4 and token not in {"cinderella", "umbrella", "gorilla", "manila", "kerala", "vila"}:
            if any(root in token for root in ("kovil", "koyil", "oor", "temple", "amman", "chennai", "madurai", "thanjavur")):
                return "ta-Latn"
        if token.endswith("oda") and len(token) > 4 and token not in {"pagoda"}:
            return "ta-Latn"
        if token.endswith("ku") and len(token) > 4 and token not in {"haiku", "baku"}:
            return "ta-Latn"

    return "en"


# -----------------------------------------------------------------------------
# 2. Question Type Classification (Multilingual: Tamil, Kannada, Tanglish, English)
# -----------------------------------------------------------------------------
def classify_question_type(text: str) -> str:
    """Classify user query into one of the 10 defined question_types.

    Codes: history, architecture, location, how_to_reach, timings_entry,
           festival, recommendation, comparison, out_of_scope, general_heritage.
    """
    t = text.lower()

    # Out of scope first
    if any(w in t for w in [
        "வானிலை", "மழை", "கிரிக்கெட்", "weather", "temperature", "forecast", "cricket",
        "ipl", "bitcoin", "crypto", "election", "politics", "python", "javascript", "coding"
    ]):
        return "out_of_scope"

    # History
    if any(w in t for w in [
        "கட்டியவர்", "கட்டியது", "கட்டிய", "யார் கட்டிய", "வரலாறு", "மன்னர்", "அரசர்",
        "காலம்", "கட்டப்பட்டது", "கட்டியது யார்", "கட்டினார்",
        "ಕಟ್ಟಿಸಿದವರು", "ಕಟ್ಟಿಸಿದ", "ಇತಿಹಾಸ", "ಯಾರು", "ಯಾವಾಗ",
        "who built", "when built", "built by", "builder", "history", "dynasty", "century",
        "chola", "pandya", "pallava", "nayak", "cheran", "raja raja", "rajendra",
        "kattiyavar", "kattina", "kattunathu", "kattiyadhu", "eppo kattunanga", "varalaaru"
    ]):
        return "history"

    # Location
    if any(w in t for w in [
        "எங்கு", "எங்கே", "இருப்பிடம்", "எங்குள்ளது", "எங்கு உள்ளது", "மாவட்டம்",
        "ಎಲ್ಲಿದೆ", "ಎಲ್ಲಿ ಇದೆ", "ಯಾವ ಜಿಲ್ಲೆ", "ಸ್ಥಳ", "ಎಲ್ಲಿ",
        "where is", "where are", "which district", "which taluk", "location", "located",
        "situated", "enga", "engae", "iruppidam", "enga irukku", "endha mavattam", "endha oor"
    ]):
        return "location"

    # Timings & entry
    if any(w in t for w in [
        "கட்டணம்", "நேரம்", "திறக்கும்", "மூடும்", "நுழைவு", "நுழைவுக் கட்டணம்", "நுழைவுக்கட்டணம்",
        "ಸಮಯ", "ಪ್ರವೇಶ", "ಶುಲ್ಕ",
        "timing", "timings", "hours", "open", "close", "ticket", "tickets", "entry fee",
        "cost", "price", "dress code", "opening hours", "closing hours", "kattanam", "neram"
    ]):
        return "timings_entry"

    # Festival
    if any(w in t for w in [
        "திருவிழா", "திருவிழாக்கள்", "உற்சவம்", "குண்டம்", "பண்டிகை", "பூஜை", "விழா",
        "ಹಬ್ಬ", "ಉತ್ಸವ", "ಜಾತ್ರೆ",
        "festival", "festivals", "feast", "utsav", "kundam", "thiruvizha", "pooja",
        "nadakkum", "brahmotsavam", "theppam", "chariot", "car festival"
    ]):
        return "festival"

    # How to reach
    if any(w in t for w in [
        "எப்படி செல்வது", "செல்லும் வழி", "செல்ல வழி", "வழி", "பேருந்து", "ரயில்",
        "ಹೇಗೆ ತಲುಪುವುದು", "ಹೇಗೆ ಹೋಗುವುದು", "ದಾರಿ",
        "how to reach", "how to go", "reach", "nearest airport", "nearest railway",
        "bus", "train", "route", "distance", "transport", "epdi ponum", "vazhi", "sellum vazhi"
    ]):
        return "how_to_reach"

    # Architecture
    if any(w in t for w in [
        "கட்டடக்கலை", "சிற்ப", "சிற்பங்கள்", "தூண்", "தூண்கள்", "கோபுரம்", "விமானம்",
        "ಶಿಲ್ಪಕಲೆ", "ವಾಸ್ತುಶಿಲ್ಪ",
        "architecture", "architectural", "style", "dravidian", "vimana", "gopuram",
        "carving", "carvings", "sculpture", "sculptures", "mandapa", "kattadakkalai"
    ]):
        return "architecture"

    # Recommendation
    if any(w in t for w in [
        "பார்க்கலாம்", "சுற்றி பார்க்க", "சுற்றிப்பார்க்க", "பரிந்துரை",
        "ನೋಡಬಹುದು", "ಯಾವ ಸ್ಥಳಗಳು",
        "recommend", "recommendation", "places to visit", "what to see", "where can i see",
        "suggest", "suggestions", "which sites", "which forts", "which temples",
        "near", "nearby", "around"
    ]):
        return "recommendation"

    # Comparison
    if any(w in t for w in [
        "ஒப்பீடு", "வித்தியாசம்", "விடவும்",
        "ಹೋಲಿಕೆ", "ವ್ಯತ್ಯಾಸ",
        "compare", "comparison", "difference between", "vs", "versus", "oppeedu"
    ]):
        return "comparison"

    return "general_heritage"


# -----------------------------------------------------------------------------
# 3. Entity Extraction & Identity Verification
# -----------------------------------------------------------------------------
def extract_entity_name(query: str) -> Optional[str]:
    """Extract candidate entity name from query by stripping question frames, verbs, and postpositions."""
    q = query.strip(' ?.,!;:-\"\'\n\t')

    prefixes = [
        r'^(?:tell\s+me\s+about|can\s+you\s+tell\s+me\s+about|information\s+about|details\s+of|details\s+about|who\s+built|when\s+was|when\s+did|where\s+is|where\s+are|what\s+is\s+the\s+history\s+of|what\s+festival\s+happens\s+at|what\s+about|history\s+of)\s+',
        r'^(?:engae|enga|eppadi|epdi|yaar|yaaru|eppo|eppothu)\s+',
        r'^(?:the\s+)',
    ]
    for p in prefixes:
        q = re.sub(p, '', q, flags=re.IGNORECASE).strip()

    suffixes = [
        r'\s+(?:la\s+enna\s+festival\s+nadakkum|enna\s+festival\s+nadakkum|festival\s+nadakkum|nadakkum)\b.*$',
        r'\s+(?:pathi\s+sollu|paththi\s+sollu|pathi\s+solunga|paththi\s+solunga|pathi|paththi|sollu|solla|solunga)\b.*$',
        r'\s+(?:kattiyavar\s+yaar|kattiyadhu\s+yaar|kattinaanga|kattiyavar|kattinaar)\b.*$',
        r'\s+(?:enga\s+irukku|engae\s+irukku|enga|engae|irukku|iruku)\b.*$',
        r'\s+(?:yellide|elli\s+ide)\b.*$',
        r'\s+(?:கட்டியவர்\s+யார்|கட்டியது\s+யார்|கட்டியவர்|கட்டியது|பற்றி\s+சொல்லு|பற்றி|எங்குள்ளது).*$',
        r'\s+(?:ಎಲ್ಲಿದೆ|ಎಲ್ಲಿ\s+ಇದೆ).*$',
    ]
    for s in suffixes:
        q = re.sub(s, '', q, flags=re.IGNORECASE).strip()

    # Strip location suffix like 'in Erode', 'at Thanjavur' if present
    q = re.sub(r'\s+(?:in|at|near)\s+[a-zA-Z\u0B80-\u0BFF]+$', '', q, flags=re.IGNORECASE).strip()

    cleaned = q.strip(' ?.,!;:-\"\'')
    if not cleaned or len(cleaned) < 3:
        return None
    # Categorical queries do not name a specific entity
    if cleaned.lower().startswith(('which forts', 'which temples', 'which monuments', 'which museums')):
        return None
    return cleaned


def verify_identity(
    db: Session,
    candidate_site_id: str,
    query: str,
    detected_district: Optional[str] = None,
) -> Tuple[bool, float, str]:
    """Verify if candidate_site matches the entity named in the user question.

    Requires:
    1. pg_trgm similarity >= 0.55 between extracted entity and candidate site's name/aliases.
    2. Candidate site district must match detected_district if a place/district is mentioned.
    Returns: (is_match, similarity_score, reason)
    """
    entity = extract_entity_name(query)
    if not entity:
        # Query does not name a specific entity (e.g. general recommendation or overview)
        return True, 1.0, "no_specific_entity_named"

    ent_lower = entity.lower().strip()

    sql = sa_text("""
        SELECT s.site_id, s.name_en, s.name_ta, l.name_en AS district_name,
               GREATEST(
                   similarity(lower(s.name_en), :ent),
                   similarity(lower(COALESCE(s.name_ta, '')), :ent),
                   COALESCE(max_alias.sim, 0.0)
               ) AS max_sim
        FROM sites s
        JOIN locations l ON s.district_id = l.location_id
        LEFT JOIN (
            SELECT site_id, MAX(similarity(alias_norm, :ent)) AS sim
            FROM site_aliases
            WHERE site_id = :sid
            GROUP BY site_id
        ) max_alias ON s.site_id = max_alias.site_id
        WHERE s.site_id = :sid
        GROUP BY s.site_id, s.name_en, s.name_ta, l.name_en, max_alias.sim;
    """)
    row = db.execute(sql, {"sid": candidate_site_id, "ent": ent_lower}).mappings().fetchone()
    if not row:
        return False, 0.0, "candidate_site_not_found"

    max_sim = float(row["max_sim"]) if row["max_sim"] is not None else 0.0
    site_district = row["district_name"]

    # 1. District check: if user mentioned a place/district, it must match
    if detected_district and site_district and detected_district.lower() != site_district.lower():
        return False, max_sim, f"district_mismatch ({site_district} != {detected_district})"

    # 2. Similarity threshold: >= 0.55
    if max_sim < 0.55:
        # Check word_similarity on aliases >= 6 chars as fallback
        w_sql = sa_text("""
            SELECT MAX(word_similarity(alias_norm, :ent))
            FROM site_aliases
            WHERE site_id = :sid AND length(alias_norm) >= 6;
        """)
        w_sim = db.execute(w_sql, {"sid": candidate_site_id, "ent": ent_lower}).scalar() or 0.0
        if w_sim >= 0.70:
            return True, float(w_sim), "word_sim_match"
        return False, max_sim, f"similarity_too_low ({max_sim:.3f} < 0.55)"

    return True, max_sim, "matched"


def detect_site(db: Session, query_text: str) -> Optional[str]:
    """Identify matching site_id from site_aliases using pg_trgm similarity.

    Requires similarity >= 0.4 on alias_norm vs normalized query text.
    """
    q_norm = query_text.lower().strip()
    sql = sa_text("""
        SELECT site_id, alias, similarity(alias_norm, :q) AS sim
        FROM site_aliases
        WHERE similarity(alias_norm, :q) >= 0.4
        ORDER BY sim DESC, length(alias_norm) DESC
        LIMIT 1;
    """)
    row = db.execute(sql, {"q": q_norm}).mappings().fetchone()
    if row and row["sim"] is not None and float(row["sim"]) >= 0.4:
        return str(row["site_id"])
    return None


def detect_district(db: Session, query_text: str) -> Optional[str]:
    """Check if query mentions any known Tamil Nadu district from locations table."""
    text_lower = query_text.lower()
    sql = sa_text("SELECT name_en, name_ta FROM locations WHERE level = 'district'")
    districts = db.execute(sql).fetchall()
    for d_en, d_ta in districts:
        if d_en and d_en.lower() in text_lower:
            return d_en
        if d_ta and d_ta in query_text:
            return d_en

    # Common aliases / variations
    if "kanyakumari" in text_lower:
        return "Kanniyakumari"
    if "tanjore" in text_lower or "thanjai" in text_lower or "தஞ்சை" in query_text:
        return "Thanjavur"
    if "trichy" in text_lower or "திருச்சி" in query_text:
        return "Tiruchirappalli"
    if "tuticorin" in text_lower:
        return "Thoothukkudi"
    return None


# -----------------------------------------------------------------------------
# 4. Vector Search & Reranking
# -----------------------------------------------------------------------------
LANG_PREFIX_TEMPLATES = {
    "ta": "எங்கள் பதிவுகளின்படி:\n\n",
    "ta-Latn": "Engal pathivugalin padi:\n\n",
    "kn": "ನಮ್ಮ ದಾಖಲೆಗಳ ಪ್ರಕಾರ:\n\n",
    "en": "From our heritage records:\n\n",
}

REJECTION_MESSAGES = {
    "ta": "மன்னிக்கவும், எங்கள் பதிவுகளில் இது பற்றிய நம்பகமான தகவல் இல்லை.",
    "ta-Latn": "Mannikkavum, engal pathivugalil idhu patriya nambagamaana thagaval illai.",
    "kn": "ಕ್ಷಮಿಸಿ, ನಮ್ಮ ದಾಖಲೆಗಳಲ್ಲಿ ಇದರ ಬಗ್ಗೆ ವಿಶ್ವಾಸಾರ್ಹ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ.",
    "en": "I could not find reliable records for this heritage query in our database.",
}

# -----------------------------------------------------------------------------
# Conversational Greetings & Welcome Responses
# -----------------------------------------------------------------------------
GREETING_WORDS = {
    "hi", "hello", "hey", "hola", "greetings", "good morning", "good afternoon",
    "good evening", "good day", "hi there", "hello there", "hey there",
    "vanakkam", "வணக்கம்", "namaskara", "namaste", "namaskaram",
    "hi bot", "hello bot", "hi llm", "hello llm",
}

GREETING_RESPONSES = {
    "en": "Hello! How can I help you today with Tamil Nadu's heritage sites, temples, monuments, and history?",
    "ta": "வணக்கம்! தமிழ்நாட்டின் வரலாற்றுச் சின்னங்கள், கோயில்கள், கோட்டைகள் மற்றும் பாரம்பரியம் பற்றி இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?",
    "ta-Latn": "Vanakkam! Tamil Nadu heritage sites, kovilgal, monuments matrum varalaaru pathi inniku ungalukku eppadi help panna mudiyum?",
    "kn": "ನಮಸ್ಕಾರ! ತಮಿಳುನಾಡಿನ ಐತಿಹಾಸಿಕ ತಾಣಗಳು, ದೇವಾಲಯಗಳು ಮತ್ತು ಪರಂಪರೆಯ ಬಗ್ಗೆ ಇಂದು ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು?",
}


GREETING_PUNCT_RE = re.compile(r"[\.,!\?;:\'\"\-]+")


def is_greeting(text: str) -> bool:
    """Check if the user input is a pure greeting rather than a specific factual query."""
    clean = re.sub(r"\s+", " ", GREETING_PUNCT_RE.sub(" ", text)).strip().lower()
    if not clean:
        return False
    if clean in GREETING_WORDS:
        return True
    tokens = clean.split()
    if len(tokens) <= 3 and tokens[0] in {"hi", "hello", "hey", "vanakkam", "வணக்கம்", "namaskara", "namaste"}:
        heritage_clues = {
            "who", "what", "where", "when", "how", "temple", "kovil", "koyil",
            "kattiyavar", "enga", "eppo", "history", "varalaaru", "festival", "timings"
        }
        if not any(w in clean for w in heritage_clues):
            return True
    return False


def search_and_rank_chunks(
    query_text: str,
    query_lang: str,
    resolved_site: Optional[str] = None,
    detected_district: Optional[str] = None,
    top_k: int = 8,
) -> List[Dict[str, Any]]:
    """Retrieve top-8 chunks from Qdrant and rank them with language preference."""
    embedder = get_embedding_service()
    query_vector = embedder.embed_query(query_text)
    qdrant = get_qdrant_client()

    filter_conditions = []
    if resolved_site:
        filter_conditions.append(
            models.FieldCondition(
                key="site_id",
                match=models.MatchValue(value=resolved_site),
            )
        )
    elif detected_district:
        filter_conditions.append(
            models.FieldCondition(
                key="district",
                match=models.MatchValue(value=detected_district),
            )
        )

    qdrant_filter = models.Filter(must=filter_conditions) if filter_conditions else None

    def _execute_qdrant_search(filter_obj: Optional[models.Filter]):
        if hasattr(qdrant, "query_points"):
            res = qdrant.query_points(
                collection_name=settings.QDRANT_COLLECTION,
                query=query_vector,
                using="dense",
                query_filter=filter_obj,
                limit=top_k,
                with_payload=True,
            )
            return res.points
        return qdrant.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=("dense", query_vector),
            query_filter=filter_obj,
            limit=top_k,
            with_payload=True,
        )

    try:
        hits = _execute_qdrant_search(qdrant_filter)
    except Exception as e:
        logger.error(f"Vector search failed with filter: {e}")
        hits = []

    # If filtered search gave no results or poor score, search unfiltered as fallback
    if resolved_site and (not hits or hits[0].score < 0.40):
        try:
            unfiltered_hits = _execute_qdrant_search(None)
            if unfiltered_hits and (not hits or unfiltered_hits[0].score > hits[0].score):
                hits = unfiltered_hits
        except Exception as e:
            logger.error(f"Fallback unfiltered vector search failed: {e}")

    # Process and rank with language preference
    candidate_chunks = []
    for hit in hits:
        payload = hit.payload or {}
        chunk_lang = payload.get("lang", "")
        raw_score = float(hit.score)

        # Compute language match boost
        lang_boost = 0.0
        if query_lang == "ta":
            if chunk_lang == "ta":
                lang_boost = 0.05
            elif chunk_lang == "en":
                lang_boost = 0.01
        elif query_lang == "ta-Latn":
            if chunk_lang == "ta":
                lang_boost = 0.05
            elif chunk_lang == "en":
                lang_boost = 0.03
        elif query_lang == "kn":
            if chunk_lang == "kn":
                lang_boost = 0.05
            elif chunk_lang == "en":
                lang_boost = 0.01
        elif query_lang == "en":
            if chunk_lang == "en":
                lang_boost = 0.05
            elif chunk_lang == "ta":
                lang_boost = 0.01

        effective_score = raw_score + lang_boost

        candidate_chunks.append({
            "chunk_id": str(hit.id),
            "raw_score": round(raw_score, 4),
            "effective_score": round(effective_score, 4),
            "site_id": payload.get("site_id", ""),
            "site_name_en": payload.get("site_name_en", ""),
            "site_name_ta": payload.get("site_name_ta"),
            "section": payload.get("section", ""),
            "lang": chunk_lang,
            "text": payload.get("text", ""),
            "trust": payload.get("trust", "unverified"),
            "origin": payload.get("origin", "curated"),
            "source_id": payload.get("source_id"),
        })

    candidate_chunks.sort(key=lambda x: x["effective_score"], reverse=True)
    return candidate_chunks


# -----------------------------------------------------------------------------
# 5. Pipeline Orchestration, Identity Gating & Answer Generation
# -----------------------------------------------------------------------------
def process_chat_message(
    db: Session,
    session_id: uuid.UUID,
    content: str,
    idempotency_key: Optional[str] = None,
) -> ChatMessageResponse:
    """Execute complete retrieval-augmented conversational pipeline with identity verification and idempotency."""
    # 0. Check in-memory idempotency cache
    if idempotency_key:
        cache_key = (str(session_id), str(idempotency_key).strip())
        if cache_key in _IDEMPOTENCY_CACHE:
            logger.info(f"Returning cached idempotent response for session {session_id}, key {idempotency_key}")
            return _IDEMPOTENCY_CACHE[cache_key]

    start_time = time.time()
    clean_content = content.strip()

    # Verify session exists
    session_row = db.execute(
        sa_text("SELECT session_id, user_id, title FROM chat_sessions WHERE session_id = :sid"),
        {"sid": session_id},
    ).mappings().fetchone()
    if not session_row:
        raise ValueError(f"Chat session '{session_id}' not found.")

    # Prevent accidental rapid double-post (within 3 seconds)
    recent_msg = db.execute(sa_text("""
        SELECT m.message_id
        FROM chat_messages m
        WHERE m.session_id = :sid AND m.role = 'user' AND m.content = :content
          AND m.created_at >= (now() - interval '3 seconds')
        ORDER BY m.created_at DESC LIMIT 1
    """), {"sid": session_id, "content": clean_content}).mappings().fetchone()
    if recent_msg:
        asst_msg = db.execute(sa_text("""
            SELECT m.message_id, m.content, m.lang, m.resolved_site, m.created_at
            FROM chat_messages m
            WHERE m.session_id = :sid AND m.role = 'assistant' AND m.reply_to = :mid
        """), {"sid": session_id, "mid": recent_msg["message_id"]}).mappings().fetchone()
        if asst_msg:
            # Reconstruct response from recent identical turn
            user_payload = UserMessagePayload(
                message_id=recent_msg["message_id"],
                session_id=session_id,
                role="user",
                content=clean_content,
                lang=detect_language(clean_content),
                question_type=classify_question_type(clean_content),
                resolved_site=asst_msg["resolved_site"],
                created_at=asst_msg["created_at"],
            )
            asst_payload = AssistantMessagePayload(
                message_id=asst_msg["message_id"],
                session_id=session_id,
                role="assistant",
                content=asst_msg["content"],
                lang=asst_msg["lang"],
                path="db",
                top_score=0.7,
                latency_ms=100,
                question_type=user_payload.question_type,
                resolved_site=asst_msg["resolved_site"],
                reply_to=recent_msg["message_id"],
                created_at=asst_msg["created_at"],
                citations=[],
            )
            resp = ChatMessageResponse(user_message=user_payload, assistant_message=asst_payload)
            if idempotency_key:
                _IDEMPOTENCY_CACHE[(str(session_id), str(idempotency_key).strip())] = resp
            return resp

    # 1. Language detection
    detected_lang = detect_language(clean_content)

    # 1b. Conversational Greeting Handler
    if is_greeting(clean_content):
        latency_ms = int((time.time() - start_time) * 1000)
        assistant_content = GREETING_RESPONSES.get(detected_lang, GREETING_RESPONSES["en"])

        # Persist greeting turn
        u_row = db.execute(sa_text("""
            INSERT INTO chat_messages (
                session_id, role, content, lang, question_type, resolved_site, reply_to, created_at
            ) VALUES (
                :session_id, 'user', :content, :lang, 'general_heritage', NULL, NULL, now()
            ) RETURNING message_id, created_at
        """), {
            "session_id": session_id,
            "content": clean_content,
            "lang": detected_lang,
        }).mappings().fetchone()
        user_message_id = u_row["message_id"]
        user_created_at = u_row["created_at"]

        a_row = db.execute(sa_text("""
            INSERT INTO chat_messages (
                session_id, role, content, lang, question_type, resolved_site, reply_to, created_at
            ) VALUES (
                :session_id, 'assistant', :content, :lang, NULL, NULL, :reply_to, now()
            ) RETURNING message_id, created_at
        """), {
            "session_id": session_id,
            "content": assistant_content,
            "lang": detected_lang,
            "reply_to": user_message_id,
        }).mappings().fetchone()
        assistant_message_id = a_row["message_id"]
        assistant_created_at = a_row["created_at"]

        db.execute(sa_text("""
            INSERT INTO query_logs (
                message_id, query_text, query_lang, rewritten_en, resolved_site,
                path, top_score, chunk_ids, latency_ms, path_detail, gemini_calls, created_at
            ) VALUES (
                :message_id, :query_text, :query_lang, NULL, NULL,
                'greeting', 1.0, NULL, :latency_ms, NULL, 0, now()
            )
        """), {
            "message_id": user_message_id,
            "query_text": clean_content,
            "query_lang": detected_lang,
            "latency_ms": latency_ms,
        })

        db.execute(sa_text("""
            UPDATE chat_sessions
            SET last_message_at = now(),
                title = CASE WHEN (title IS NULL OR title = 'New Chat') THEN :title ELSE title END
            WHERE session_id = :sid
        """), {
            "sid": session_id,
            "title": clean_content[:50],
        })
        db.commit()

        user_payload = UserMessagePayload(
            message_id=user_message_id,
            session_id=session_id,
            role="user",
            content=clean_content,
            lang=detected_lang,
            question_type="general_heritage",
            resolved_site=None,
            created_at=user_created_at,
        )
        asst_payload = AssistantMessagePayload(
            message_id=assistant_message_id,
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            lang=detected_lang,
            path="greeting",
            top_score=1.0,
            latency_ms=latency_ms,
            question_type="general_heritage",
            resolved_site=None,
            reply_to=user_message_id,
            created_at=assistant_created_at,
            citations=[],
        )
        resp = ChatMessageResponse(user_message=user_payload, assistant_message=asst_payload)
        if idempotency_key:
            _IDEMPOTENCY_CACHE[(str(session_id), str(idempotency_key).strip())] = resp
        return resp

    # 2. Question type classification
    q_type = classify_question_type(clean_content)

    # 3. Entity linking: site detection and district resolution
    resolved_site = detect_site(db, clean_content)
    detected_district = detect_district(db, clean_content)

    # 4. Vector search
    ranked_chunks = search_and_rank_chunks(
        query_text=clean_content,
        query_lang=detected_lang,
        resolved_site=resolved_site,
        detected_district=detected_district,
        top_k=8,
    )

    top_score = ranked_chunks[0]["raw_score"] if ranked_chunks else 0.0

    # 5. Identity Verification (Ground-truth entity check)
    identity_valid = True
    identity_reason = "ok"
    if ranked_chunks:
        candidate_site = ranked_chunks[0]["site_id"]
        identity_valid, id_sim, identity_reason = verify_identity(
            db, candidate_site, clean_content, detected_district
        )
        if not identity_valid:
            logger.info(
                f"Identity check rejected candidate '{candidate_site}' for query '{clean_content}': {identity_reason} (sim={id_sim:.3f})"
            )

    path_detail: Optional[Dict[str, Any]] = None
    gemini_calls_count = 0

    # 6. Determine path and extractive / synthesized answer
    # Initial check: top_score < 0.45 or identity mismatch or no initial hits
    needs_augmentation = (q_type != "out_of_scope") and (top_score < 0.45 or not ranked_chunks or not identity_valid)

    if needs_augmentation:
        extracted_entity = extract_entity_name(clean_content)
        if extracted_entity:
            logger.info(f"Triggering IC3-WB self-augmentation for entity candidate: '{extracted_entity}'")
            try:
                aug_res = run_augmentation_pipeline(
                    query=clean_content,
                    message_id=None,
                    lang=detected_lang,
                    db=db,
                )
                gemini_calls_count += aug_res.gemini_calls
                path_detail = {
                    "augment_job_id": str(aug_res.job_id),
                    "candidate_key": aug_res.candidate_key,
                    "status": aug_res.status,
                    "decision_reason": aug_res.decision_reason,
                }

                if aug_res.status == "verified" and aug_res.site_id:
                    # Re-run Qdrant retrieval for this query (it will now find the newly written chunks!)
                    new_ranked = search_and_rank_chunks(
                        query_text=clean_content,
                        query_lang=detected_lang,
                        resolved_site=aug_res.site_id,
                        detected_district=detected_district,
                        top_k=8,
                    )
                    if new_ranked:
                        ranked_chunks = new_ranked
                        top_score = ranked_chunks[0]["raw_score"]
                        path = "web_consensus"
                        effective_resolved_site = aug_res.site_id

                        # Select best 1-2 chunks
                        selected_chunks = [ranked_chunks[0]]
                        if len(ranked_chunks) > 1 and ranked_chunks[1]["site_id"] == aug_res.site_id:
                            selected_chunks.append(ranked_chunks[1])

                        citations = []
                        used_chunk_ids = []
                        for rank, chunk in enumerate(selected_chunks, start=1):
                            cit = CitationItem(
                                chunk_id=chunk["chunk_id"],
                                site_id=chunk["site_id"],
                                site_name_en=chunk["site_name_en"],
                                site_name_ta=chunk["site_name_ta"],
                                section=chunk["section"],
                                lang=chunk["lang"],
                                score=chunk["raw_score"],
                                trust=chunk["trust"],
                                origin=chunk["origin"],
                                source_url=None,
                            )
                            citations.append(cit)
                            used_chunk_ids.append(chunk["chunk_id"])

                        # Synthesize answer using Gemini + newly verified chunks
                        facts_for_synth = [
                            {"field": c["section"], "value": c["text"], "source_url": c["origin"]}
                            for c in selected_chunks
                        ]
                        assistant_content = gemini_synthesize_answer(clean_content, facts_for_synth, lang=detected_lang)
                        gemini_calls_count += 1
                    else:
                        path = "rejected"
                        assistant_content = REJECTION_MESSAGES.get(detected_lang, REJECTION_MESSAGES["en"])
                        selected_chunks = []
                        citations = []
                        used_chunk_ids = []
                        effective_resolved_site = None
                else:
                    path = "rejected"
                    assistant_content = REJECTION_MESSAGES.get(detected_lang, REJECTION_MESSAGES["en"])
                    selected_chunks = []
                    citations = []
                    used_chunk_ids = []
                    effective_resolved_site = None
            except Exception as e:
                logger.error(f"Error during on-demand self-augmentation: {e}", exc_info=True)
                path = "rejected"
                assistant_content = REJECTION_MESSAGES.get(detected_lang, REJECTION_MESSAGES["en"])
                selected_chunks = []
                citations = []
                used_chunk_ids = []
                effective_resolved_site = None
        else:
            path = "rejected"
            assistant_content = REJECTION_MESSAGES.get(detected_lang, REJECTION_MESSAGES["en"])
            selected_chunks = []
            citations = []
            used_chunk_ids = []
            effective_resolved_site = None
    elif q_type == "out_of_scope" or not ranked_chunks:
        path = "rejected"
        assistant_content = REJECTION_MESSAGES.get(detected_lang, REJECTION_MESSAGES["en"])
        selected_chunks = []
        citations = []
        used_chunk_ids = []
        effective_resolved_site = None
    else:
        path = "db"
        prefix = LANG_PREFIX_TEMPLATES.get(detected_lang, LANG_PREFIX_TEMPLATES["en"])
        effective_resolved_site = resolved_site or ranked_chunks[0]["site_id"]

        # Select best 1-2 chunks
        selected_chunks = [ranked_chunks[0]]
        if len(ranked_chunks) > 1:
            second_chunk = ranked_chunks[1]
            if second_chunk["site_id"] == ranked_chunks[0]["site_id"] and second_chunk["raw_score"] >= 0.45:
                selected_chunks.append(second_chunk)

        body_texts = [c["text"].strip() for c in selected_chunks]
        assistant_content = prefix + "\n\n".join(body_texts)

        citations = []
        used_chunk_ids = []
        for rank, chunk in enumerate(selected_chunks, start=1):
            source_url = None
            if chunk.get("source_id"):
                src_row = db.execute(
                    sa_text("SELECT url FROM sources WHERE source_id = :sid"),
                    {"sid": chunk["source_id"]},
                ).mappings().fetchone()
                if src_row:
                    source_url = src_row["url"]

            cit = CitationItem(
                chunk_id=chunk["chunk_id"],
                site_id=chunk["site_id"],
                site_name_en=chunk["site_name_en"],
                site_name_ta=chunk["site_name_ta"],
                section=chunk["section"],
                lang=chunk["lang"],
                score=chunk["raw_score"],
                trust=chunk["trust"],
                origin=chunk["origin"],
                source_url=source_url,
            )
            citations.append(cit)
            used_chunk_ids.append(chunk["chunk_id"])

    latency_ms = int((time.time() - start_time) * 1000)

    # 7. Database persistence
    # 7a. User message
    user_msg_stmt = sa_text("""
        INSERT INTO chat_messages (
            session_id, role, content, lang, question_type, resolved_site, reply_to, created_at
        ) VALUES (
            :session_id, 'user', :content, :lang, :q_type, :resolved_site, NULL, now()
        ) RETURNING message_id, created_at
    """)
    u_row = db.execute(user_msg_stmt, {
        "session_id": session_id,
        "content": clean_content,
        "lang": detected_lang,
        "q_type": q_type,
        "resolved_site": effective_resolved_site,
    }).mappings().fetchone()
    user_message_id = u_row["message_id"]
    user_created_at = u_row["created_at"]

    # 7b. Assistant message
    asst_msg_stmt = sa_text("""
        INSERT INTO chat_messages (
            session_id, role, content, lang, question_type, resolved_site, reply_to, created_at
        ) VALUES (
            :session_id, 'assistant', :content, :lang, NULL, :resolved_site, :reply_to, now()
        ) RETURNING message_id, created_at
    """)
    a_row = db.execute(asst_msg_stmt, {
        "session_id": session_id,
        "content": assistant_content,
        "lang": detected_lang,
        "resolved_site": effective_resolved_site,
        "reply_to": user_message_id,
    }).mappings().fetchone()
    assistant_message_id = a_row["message_id"]
    assistant_created_at = a_row["created_at"]

    # 7c. Citations
    cit_stmt = sa_text("""
        INSERT INTO message_citations (message_id, chunk_id, rank, score)
        VALUES (:message_id, :chunk_id, :rank, :score)
        ON CONFLICT (message_id, chunk_id) DO NOTHING
    """)
    for rank, cit in enumerate(citations, start=1):
        db.execute(cit_stmt, {
            "message_id": assistant_message_id,
            "chunk_id": uuid.UUID(cit.chunk_id),
            "rank": rank,
            "score": cit.score,
        })

    # 7d. Query logs
    log_stmt = sa_text("""
        INSERT INTO query_logs (
            message_id, query_text, query_lang, rewritten_en, resolved_site,
            path, top_score, chunk_ids, latency_ms, path_detail, gemini_calls, created_at
        ) VALUES (
            :message_id, :query_text, :query_lang, NULL, :resolved_site,
            :path, :top_score, :chunk_ids, :latency_ms, CAST(:path_detail AS jsonb), :gemini_calls, now()
        )
    """)
    db.execute(log_stmt, {
        "message_id": user_message_id,
        "query_text": clean_content,
        "query_lang": detected_lang,
        "resolved_site": effective_resolved_site,
        "path": path,
        "top_score": top_score,
        "chunk_ids": [uuid.UUID(cid) for cid in used_chunk_ids] if used_chunk_ids else None,
        "latency_ms": latency_ms,
        "path_detail": json.dumps(path_detail) if path_detail else None,
        "gemini_calls": gemini_calls_count,
    })

    # 7e. Session title
    current_title = session_row["title"]
    if not current_title or current_title.strip() == "" or current_title.lower() == "new chat":
        short_title = clean_content[:60].strip()
        db.execute(
            sa_text("UPDATE chat_sessions SET title = :title, last_message_at = now() WHERE session_id = :sid"),
            {"title": short_title, "sid": session_id},
        )
    else:
        db.execute(
            sa_text("UPDATE chat_sessions SET last_message_at = now() WHERE session_id = :sid"),
            {"sid": session_id},
        )

    db.commit()

    user_payload = UserMessagePayload(
        message_id=user_message_id,
        session_id=session_id,
        role="user",
        content=clean_content,
        lang=detected_lang,
        question_type=q_type,
        resolved_site=effective_resolved_site,
        created_at=user_created_at,
    )

    assistant_payload = AssistantMessagePayload(
        message_id=assistant_message_id,
        session_id=session_id,
        role="assistant",
        content=assistant_content,
        lang=detected_lang,
        path=path,
        top_score=top_score,
        latency_ms=latency_ms,
        question_type=q_type,
        resolved_site=effective_resolved_site,
        reply_to=user_message_id,
        created_at=assistant_created_at,
        citations=citations,
    )

    response = ChatMessageResponse(
        user_message=user_payload,
        assistant_message=assistant_payload,
    )

    # Cache for idempotency if key was provided
    if idempotency_key:
        _IDEMPOTENCY_CACHE[(str(session_id), str(idempotency_key).strip())] = response

    return response
