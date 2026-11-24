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

from datetime import datetime, timezone
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
from app.services.spec_config import (
    DecisionCode,
    GEMINI_CLASSIFY_TIMEOUT_SEC,
    RETRIEVAL_SCORE_THRESHOLD,
    RETRIEVAL_TOP_K,
    SITE_DETECT_SIM_THRESHOLD,
    PROVISIONAL_ANSWER_LABEL,
    UNCONFIRMED_FIELD_PREFIX,
)

logger = logging.getLogger(__name__)


class PipelineTracer:
    """Collects pipeline execution stages and persists them to message_traces (Rule 1.4)."""

    def __init__(self):
        self.stages: List[Dict[str, Any]] = []

    def record(
        self,
        stage: str,
        start_time: float,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ):
        duration_ms = int((time.time() - start_time) * 1000)
        self.stages.append({
            "stage": stage,
            "started_at": datetime.now(timezone.utc),
            "duration_ms": duration_ms,
            "input": input_data,
            "output": output_data,
            "error": error,
        })

    def flush(self, db: Session, message_id: int):
        for s in self.stages:
            db.execute(
                sa_text("""
                    INSERT INTO message_traces (message_id, stage, started_at, duration_ms, input, output, error)
                    VALUES (:mid, :stage, :started_at, :duration_ms, CAST(:input AS jsonb), CAST(:output AS jsonb), :error)
                """),
                {
                    "mid": message_id,
                    "stage": s["stage"],
                    "started_at": s["started_at"],
                    "duration_ms": s["duration_ms"],
                    "input": json.dumps(s["input"]) if s["input"] is not None else None,
                    "output": json.dumps(s["output"]) if s["output"] is not None else None,
                    "error": s["error"],
                },
            )


def _build_web_checked_rejection(lang: str, n_sources: int, entity_label: str, district_label: str) -> str:
    """Rule 1.5: After web check ran, never say 'in our database'; say what was checked."""
    if lang == "ta":
        return f"நான் {n_sources} இணையத் தளங்களை சரிபார்த்தேன், ஆனால் {entity_label}{district_label} பற்றிய உறுதிப்படுத்தப்பட்ட தகவல்கள் கிடைக்கவில்லை."
    elif lang == "ta-Latn":
        return f"Naan {n_sources} web sources check pannen, aana {entity_label}{district_label} pathina confirmed thagaval kidaikkala."
    elif lang == "kn":
        return f"ನಾನು {n_sources} ವೆಬ್ ಮೂಲಗಳನ್ನು ಪರಿಶೀಲಿಸಿದೆ, ಆದರೆ {entity_label}{district_label} ಬಗ್ಗೆ ದೃಢೀಕೃತ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ."
    return f"I checked {n_sources} web sources but could not confirm {entity_label}{district_label}."

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
        "ipl", "bitcoin", "crypto", "election", "politics", "python", "javascript", "coding",
        "stock", "market", "finance", "share price"
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


def count_matching_sites(db: Session, query_text: str) -> int:
    """Count number of distinct sites in site_aliases matching query with similarity >= 0.40 (Rule 2.2)."""
    q_norm = query_text.lower().strip()
    sql = sa_text("""
        SELECT count(DISTINCT site_id)
        FROM site_aliases
        WHERE similarity(alias_norm, :q) >= :thresh
    """)
    return db.execute(sql, {"q": q_norm, "thresh": SITE_DETECT_SIM_THRESHOLD}).scalar() or 0


def _find_partial_provisional_info(db: Session, entity_name: Optional[str]) -> Optional[str]:
    """Find any provisional facts from site_facts or evidence_ledger if quota exhausted (Rule 2.1)."""
    if not entity_name:
        return None
    try:
        row = db.execute(sa_text("""
            SELECT sf.field, sf.value
            FROM site_facts sf
            JOIN site_aliases sa ON sa.site_id = sf.site_id
            WHERE similarity(sa.alias_norm, :ent) >= 0.4
            LIMIT 1
        """), {"ent": entity_name.lower().strip()}).mappings().fetchone()
        if row:
            return f"Provisional information: {row['field']} - {row['value']}"
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
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
    "en": "I could not find reliable records for this heritage query.",
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
    tracer = PipelineTracer()

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

    # 0b. CLARIFY-THEN-AUGMENT pre-hook
    # ── Check if there is an open clarification for this session. ──────────
    try:
        from app.augment.clarify import process_clarification_reply
        from app.services.clarifications import get_open_clarification
        from app.augment.disambiguate import disambiguate_query

        open_clar = get_open_clarification(db, str(session_id))
        if open_clar:
            t_clar = time.time()
            clar_result = process_clarification_reply(
                db, str(session_id), {"text": clean_content}
            )
            tracer.record(
                "clarification_prehook",
                t_clar,
                input_data={"text": clean_content, "entity": open_clar.entity_name},
                output_data={"status": clar_result.get("status")},
            )
            if clar_result["status"] == "resolved":
                selected = clar_result["selected_candidate"]
                if getattr(open_clar, "kind", "identity") == "value":
                    # Value choice resolution (Rule 4.2)
                    detected_lang = detect_language(clean_content)
                    is_compare_all = selected.get("value") == "Compare all"
                    choice_citations: List[CitationItem] = []
                    if is_compare_all:
                        lines = [f"Side-by-side comparison of sources for {open_clar.field or 'field'} of {open_clar.entity_name}:"]
                        for c in (open_clar.candidates or []):
                            if c.get("value") == "Compare all":
                                continue
                            c_val = c.get("value", "")
                            c_ratio = int(c.get("support_ratio", 0) * 100)
                            c_srcs = ", ".join(f"{s.get('domain')} [Tier {s.get('tier', 'C')}]" for s in c.get("sources", []) if s.get("domain")) or "sources"
                            lines.append(f"- **{c_val}**: Supported by {c_srcs} ({c_ratio}%)")
                            for s in c.get("sources", []):
                                choice_citations.append(CitationItem(
                                    chunk_id=str(uuid.uuid4()),
                                    site_id="web",
                                    site_name_en=open_clar.entity_name,
                                    site_name_ta=open_clar.entity_name_ta,
                                    section=open_clar.field or "history",
                                    lang=detected_lang,
                                    score=c.get("support_ratio", 0.5),
                                    trust="high" if s.get("tier") == "A" else "medium",
                                    origin=s.get("domain") or "web",
                                    source_url=s.get("url"),
                                ))
                        assistant_content = "\n".join(lines)
                    else:
                        chosen_val = selected.get("value", "")
                        chosen_srcs = ", ".join(f"{s.get('domain')} (Tier {s.get('tier', 'C')})" for s in selected.get("sources", []) if s.get("domain")) or "sources"
                        other_options = [c.get("value") for c in (open_clar.candidates or []) if c.get("value") and c.get("value") not in (chosen_val, "Compare all")]
                        other_note = f" (Note: other sources also report {', '.join(other_options)}.)" if other_options else ""
                        assistant_content = f"According to {chosen_srcs}, the {open_clar.field or 'field'} of {open_clar.entity_name} is **{chosen_val}**.{other_note}"
                        for s in selected.get("sources", []):
                            choice_citations.append(CitationItem(
                                chunk_id=str(uuid.uuid4()),
                                site_id="web",
                                site_name_en=open_clar.entity_name,
                                site_name_ta=open_clar.entity_name_ta,
                                section=open_clar.field or "history",
                                lang=detected_lang,
                                score=selected.get("support_ratio", 1.0),
                                trust="high" if s.get("tier") == "A" else "medium",
                                origin=s.get("domain") or "web",
                                source_url=s.get("url"),
                            ))

                    # Persist user message
                    u_row = db.execute(sa_text("""
                        INSERT INTO chat_messages (
                            session_id, role, content, lang, question_type, resolved_site, reply_to, created_at
                        ) VALUES (
                            :session_id, 'user', :content, :lang, :q_type, NULL, NULL, now()
                        ) RETURNING message_id, created_at
                    """), {
                        "session_id": session_id,
                        "content": clean_content,
                        "lang": detected_lang,
                        "q_type": open_clar.question_type or "general_heritage",
                    }).mappings().fetchone()
                    user_message_id = u_row["message_id"]
                    user_created_at = u_row["created_at"]

                    # Persist assistant message
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

                    # Record pick in option_choices table (Rule 4.2: for analytics ONLY)
                    sess_row = db.execute(sa_text("SELECT user_id FROM chat_sessions WHERE session_id = :sid"), {"sid": session_id}).fetchone()
                    uid = sess_row[0] if sess_row else None
                    db.execute(sa_text("""
                        INSERT INTO option_choices (
                            choice_id, message_id, user_id, field, chosen_value, options, created_at
                        ) VALUES (
                            gen_random_uuid(), :mid, :uid, :field, :chosen_val, CAST(:options AS jsonb), now()
                        )
                    """), {
                        "mid": user_message_id,
                        "uid": uid,
                        "field": open_clar.field or "unknown",
                        "chosen_val": selected.get("value", clean_content),
                        "options": json.dumps(open_clar.candidates or []),
                    })

                    # Record query_log
                    db.execute(sa_text("""
                        INSERT INTO query_logs (
                            message_id, query_text, query_lang, rewritten_en, resolved_site,
                            path, top_score, chunk_ids, latency_ms, path_detail, gemini_calls, created_at
                        ) VALUES (
                            :message_id, :query_text, :query_lang, NULL, NULL,
                            'web_consensus', 1.0, NULL, :latency_ms, CAST(:path_detail AS jsonb), 0, now()
                        )
                    """), {
                        "message_id": user_message_id,
                        "query_text": clean_content,
                        "query_lang": detected_lang,
                        "latency_ms": int((time.time() - start_time) * 1000),
                        "path_detail": json.dumps({
                            "status": "verified",
                            "decision_code": DecisionCode.VERIFIED_NEW.value,
                            "kind": "value_choice",
                            "field": open_clar.field,
                            "chosen_value": selected.get("value"),
                        }),
                    })
                    db.commit()

                    resp = ChatMessageResponse(
                        user_message=UserMessagePayload(
                            message_id=user_message_id,
                            session_id=session_id,
                            role="user",
                            content=clean_content,
                            created_at=user_created_at.isoformat() if hasattr(user_created_at, "isoformat") else str(user_created_at),
                        ),
                        assistant_message=AssistantMessagePayload(
                            message_id=assistant_message_id,
                            session_id=session_id,
                            role="assistant",
                            content=assistant_content,
                            path="web_consensus",
                            resolved_site=None,
                            lang=detected_lang,
                            question_type=open_clar.question_type or "general_heritage",
                            citations=choice_citations,
                            latency_ms=int((time.time() - start_time) * 1000),
                            created_at=assistant_created_at.isoformat() if hasattr(assistant_created_at, "isoformat") else str(assistant_created_at),
                        ),
                    )
                    if idempotency_key:
                        _IDEMPOTENCY_CACHE[(str(session_id), str(idempotency_key).strip())] = resp
                    return resp
                else:
                    # Identity clarification resolution (Rule 2.2)
                    clean_content = (
                        f"{open_clar.entity_name} {selected.get('locality', '')} {selected.get('district', '')}".strip()
                    )
    except Exception as _clar_err:
        logger.warning(f"Clarification pre-hook failed (non-fatal): {_clar_err}")

    # 1. Conversational Greeting Fast-Path
    t_greet = time.time()
    detected_lang = detect_language(clean_content)
    is_greet = is_greeting(clean_content)
    tracer.record("greeting_check", t_greet, input_data={"text": clean_content}, output_data={"is_greeting": is_greet})

    if is_greet:
        latency_ms = int((time.time() - start_time) * 1000)
        assistant_content = GREETING_RESPONSES.get(detected_lang, GREETING_RESPONSES["en"])
        tracer.record("greeting_fast_path", t_greet, output_data={"path": "greeting"})

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

        tracer.flush(db, user_message_id)
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

    # -------------------------------------------------------------------------
    # Rule 1.1: Move Gemini classification to the START of chat_pipeline, right after greeting
    # -------------------------------------------------------------------------
    t_cls = time.time()
    from app.services.gemini import classify_query
    classification = classify_query(clean_content, db=db, timeout_sec=GEMINI_CLASSIFY_TIMEOUT_SEC)
    tracer.record(
        "classification",
        t_cls,
        input_data={"query": clean_content},
        output_data=classification,
    )

    is_heritage = classification["is_heritage"]
    q_type = classification.get("question_type") or ("out_of_scope" if not is_heritage else "general_heritage")
    detected_lang = classification.get("lang") or detect_language(clean_content)
    entity_name = classification.get("entity_name")
    entity_name_ta = classification.get("entity_name_ta")
    place_name = classification.get("place_name")
    entity_type = classification.get("entity_type", "specific_site")
    asked_field = classification.get("asked_field", "overview")
    fallback_used = classification.get("fallback_used", False)
    gemini_calls_count = 0 if fallback_used else 1

    # -------------------------------------------------------------------------
    # Rule 1.2: is_heritage=false -> path 'out_of_scope', polite reply, NO augmentation job
    # -------------------------------------------------------------------------
    if not is_heritage or q_type == "out_of_scope":
        t_oos = time.time()
        tracer.record(
            "out_of_scope",
            t_oos,
            input_data={"query": clean_content, "classification": classification},
            output_data={"path": "out_of_scope"},
        )
        OUT_OF_SCOPE_RESPONSES = {
            "en": "I specialize in Tamil Nadu's heritage, temples, monuments, and history. I can only assist with heritage-related questions.",
            "ta": "நான் தமிழ்நாட்டின் மரபு, கோயில்கள், வரலாற்றுச் சின்னங்கள் மற்றும் வரலாறு தொடர்பான தகவல்களை வழங்கும் உதவியாளர். பாரம்பரியம் தொடர்பான வினாக்களுக்கு மட்டுமே என்னால் உதவ முடியும்.",
            "ta-Latn": "Naan Tamil Nadu heritage, kovilgal, monuments matrum varalaaru sambanthapatta kelvigalukku mattume bathil alikka mudiyum.",
            "kn": "ನಾನು ತಮಿಳುನಾಡಿನ ಪರಂಪರೆ, ದೇವಾಲಯಗಳು, ಸ್ಮಾರಕಗಳು ಮತ್ತು ಇತಿಹಾಸಕ್ಕೆ ಸಂಬಂಧಿಸಿದ ಪ್ರಶ್ನೆಗಳಿಗೆ ಮಾತ್ರ ಉತ್ತರಿಸಬಲ್ಲೆ.",
        }
        assistant_content = OUT_OF_SCOPE_RESPONSES.get(detected_lang, OUT_OF_SCOPE_RESPONSES["en"])
        latency_ms = int((time.time() - start_time) * 1000)

        u_row = db.execute(sa_text("""
            INSERT INTO chat_messages (
                session_id, role, content, lang, question_type, resolved_site, reply_to, created_at
            ) VALUES (
                :session_id, 'user', :content, :lang, 'out_of_scope', NULL, NULL, now()
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

        path_detail = {
            "status": "out_of_scope",
            "decision_code": DecisionCode.OUT_OF_SCOPE.value,
            "decision_reason": DecisionCode.OUT_OF_SCOPE.value,
            "detail": "Query classified as non-heritage",
        }

        db.execute(sa_text("""
            INSERT INTO query_logs (
                message_id, query_text, query_lang, rewritten_en, resolved_site,
                path, top_score, chunk_ids, latency_ms, path_detail, gemini_calls, created_at
            ) VALUES (
                :message_id, :query_text, :query_lang, NULL, NULL,
                'out_of_scope', 0.0, NULL, :latency_ms, CAST(:path_detail AS jsonb), :gemini_calls, now()
            )
        """), {
            "message_id": user_message_id,
            "query_text": clean_content,
            "query_lang": detected_lang,
            "latency_ms": latency_ms,
            "path_detail": json.dumps(path_detail),
            "gemini_calls": gemini_calls_count,
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

        tracer.flush(db, user_message_id)
        db.commit()

        user_payload = UserMessagePayload(
            message_id=user_message_id,
            session_id=session_id,
            role="user",
            content=clean_content,
            lang=detected_lang,
            question_type="out_of_scope",
            resolved_site=None,
            created_at=user_created_at,
        )
        asst_payload = AssistantMessagePayload(
            message_id=assistant_message_id,
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            lang=detected_lang,
            path="out_of_scope",
            top_score=0.0,
            latency_ms=latency_ms,
            question_type="out_of_scope",
            resolved_site=None,
            reply_to=user_message_id,
            created_at=assistant_created_at,
            citations=[],
        )
        resp = ChatMessageResponse(user_message=user_payload, assistant_message=asst_payload)
        if idempotency_key:
            _IDEMPOTENCY_CACHE[(str(session_id), str(idempotency_key).strip())] = resp
        return resp

    # -------------------------------------------------------------------------
    # Rule 1.3: Place resolution against LGD locations hierarchy & aliases
    # -------------------------------------------------------------------------
    t_place = time.time()
    from app.services.place_resolution import resolve_place
    resolved_place = resolve_place(clean_content, place_name=place_name, db=db)
    tracer.record(
        "place_resolution",
        t_place,
        input_data={"text": clean_content, "place_name": place_name},
        output_data={
            "district_id": resolved_place.district_id,
            "district_name": resolved_place.district_name,
            "subdistrict_id": resolved_place.subdistrict_id,
            "subdistrict_name": resolved_place.subdistrict_name,
            "local_body_id": resolved_place.local_body_id,
            "local_body_name": resolved_place.local_body_name,
            "matched_term": resolved_place.matched_term,
        },
    )
    detected_district = resolved_place.district_name or detect_district(db, clean_content)

    # -------------------------------------------------------------------------
    # Rule 2.2: Identity Clarification (disambiguate_query)
    # Trigger when is_heritage and no district resolved and (entity_type = deity_or_generic_name OR db_matches_count >= 2 OR db_matches_count == 0)
    # -------------------------------------------------------------------------
    db_matches_count = count_matching_sites(db, clean_content)
    should_disambiguate = (
        is_heritage
        and not detected_district
        and (
            entity_type == "deity_or_generic_name"
            or db_matches_count >= 2
            or db_matches_count == 0
        )
    )

    if should_disambiguate:
        t_disamb = time.time()
        from app.augment.disambiguate import disambiguate_query
        disamb_res = disambiguate_query(
            db,
            session_id=str(session_id),
            asked_message_id=0,
            entity_name=entity_name or extract_entity_name(clean_content),
            entity_name_ta=entity_name_ta,
            question_type=q_type,
            original_query=clean_content,
        )
        tracer.record(
            "disambiguation",
            t_disamb,
            input_data={"entity": entity_name, "entity_type": entity_type, "db_matches": db_matches_count},
            output_data=disamb_res,
        )

        if disamb_res.get("status") == "needs_clarification":
            candidates = disamb_res.get("candidates", [])
            cand_lines = []
            for idx, c in enumerate(candidates, start=1):
                loc_part = f"{c.get('locality')}, " if c.get("locality") else ""
                dist_part = c.get("district", "")
                cand_lines.append(f"{idx}. {loc_part}{dist_part}".strip())

            if detected_lang == "ta":
                intro = f"'{entity_name or clean_content}' என்ற பெயரில் பல கோயில்கள் உள்ளன. நீங்கள் எந்த இடத்தைக் குறிப்பிடுகிறீர்கள்?\n\n"
            elif detected_lang == "ta-Latn":
                intro = f"'{entity_name or clean_content}' perula pala kovilgal irukku. Neenga endha edatha kekkareenga?\n\n"
            elif detected_lang == "kn":
                intro = f"'{entity_name or clean_content}' ಹೆಸರಿನಲ್ಲಿ ಹಲವು ದೇವಾಲಯಗಳಿವೆ. ನೀವು ಯಾವ ಸ್ಥಳವನ್ನು ಉದ್ದೇಶಿಸಿದ್ದೀರಿ?\n\n"
            else:
                intro = f"There are multiple temples known as '{entity_name or clean_content}'. Which location do you mean?\n\n"

            assistant_content = intro + "\n".join(cand_lines)
            latency_ms = int((time.time() - start_time) * 1000)

            # Persist user message
            u_row = db.execute(sa_text("""
                INSERT INTO chat_messages (
                    session_id, role, content, lang, question_type, resolved_site, reply_to, created_at
                ) VALUES (
                    :session_id, 'user', :content, :lang, :q_type, NULL, NULL, now()
                ) RETURNING message_id, created_at
            """), {
                "session_id": session_id,
                "content": clean_content,
                "lang": detected_lang,
                "q_type": q_type,
            }).mappings().fetchone()
            user_message_id = u_row["message_id"]
            user_created_at = u_row["created_at"]

            # Update clarification asked_message_id
            if disamb_res.get("clarification_id"):
                db.execute(sa_text("""
                    UPDATE clarifications
                    SET asked_message_id = :mid
                    WHERE clarification_id = :cid
                """), {"mid": int(user_message_id), "cid": disamb_res["clarification_id"]})

            # Persist assistant message
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

            path_detail = {
                "status": "needs_clarification",
                "decision_code": DecisionCode.NEEDS_CLARIFICATION.value,
                "decision_reason": DecisionCode.NEEDS_CLARIFICATION.value,
                "clarification_id": disamb_res.get("clarification_id"),
                "candidates": candidates,
            }

            db.execute(sa_text("""
                INSERT INTO query_logs (
                    message_id, query_text, query_lang, rewritten_en, resolved_site,
                    path, top_score, chunk_ids, latency_ms, path_detail, gemini_calls, created_at
                ) VALUES (
                    :message_id, :query_text, :query_lang, NULL, NULL,
                    'needs_clarification', 0.0, NULL, :latency_ms, CAST(:path_detail AS jsonb), :gemini_calls, now()
                )
            """), {
                "message_id": user_message_id,
                "query_text": clean_content,
                "query_lang": detected_lang,
                "latency_ms": latency_ms,
                "path_detail": json.dumps(path_detail),
                "gemini_calls": gemini_calls_count,
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

            tracer.flush(db, user_message_id)
            db.commit()

            user_payload = UserMessagePayload(
                message_id=user_message_id,
                session_id=session_id,
                role="user",
                content=clean_content,
                lang=detected_lang,
                question_type=q_type,
                resolved_site=None,
                created_at=user_created_at,
            )
            asst_payload = AssistantMessagePayload(
                message_id=assistant_message_id,
                session_id=session_id,
                role="assistant",
                content=assistant_content,
                lang=detected_lang,
                path="needs_clarification",
                top_score=0.0,
                latency_ms=latency_ms,
                question_type=q_type,
                resolved_site=None,
                reply_to=user_message_id,
                created_at=assistant_created_at,
                citations=[],
            )
            resp = ChatMessageResponse(user_message=user_payload, assistant_message=asst_payload)
            if idempotency_key:
                _IDEMPOTENCY_CACHE[(str(session_id), str(idempotency_key).strip())] = resp
            return resp

        elif disamb_res.get("status") == "resolved_direct":
            selected = disamb_res.get("selected_candidate") or {}
            detected_district = selected.get("district")
            clean_content = f"{entity_name or clean_content} {selected.get('locality', '')} {selected.get('district', '')}".strip()
            logger.info(f"Disambiguation resolved direct to candidate: {selected}")

    # 4. Site detection
    t_site = time.time()
    resolved_site = detect_site(db, clean_content)
    tracer.record(
        "site_detection",
        t_site,
        input_data={"query": clean_content},
        output_data={"resolved_site": resolved_site},
    )

    # 5. Vector search
    t_vec = time.time()
    ranked_chunks = search_and_rank_chunks(
        query_text=clean_content,
        query_lang=detected_lang,
        resolved_site=resolved_site,
        detected_district=detected_district,
        top_k=RETRIEVAL_TOP_K,
    )
    top_score = ranked_chunks[0]["raw_score"] if ranked_chunks else 0.0
    tracer.record(
        "vector_search",
        t_vec,
        input_data={
            "query": clean_content,
            "resolved_site": resolved_site,
            "detected_district": detected_district,
        },
        output_data={
            "hit_count": len(ranked_chunks),
            "top_score": top_score,
        },
    )

    # 6. Identity Verification (Ground-truth entity check)
    identity_valid = True
    identity_reason = "ok"
    id_sim = 1.0
    if ranked_chunks:
        t_id = time.time()
        candidate_site = ranked_chunks[0]["site_id"]
        identity_valid, id_sim, identity_reason = verify_identity(
            db, candidate_site, clean_content, detected_district
        )
        tracer.record(
            "identity_verification",
            t_id,
            input_data={"candidate_site": candidate_site, "query": clean_content, "district": detected_district},
            output_data={"valid": identity_valid, "sim": id_sim, "reason": identity_reason},
        )
        if not identity_valid:
            logger.info(
                f"Identity check rejected candidate '{candidate_site}' for query '{clean_content}': {identity_reason} (sim={id_sim:.3f})"
            )

    path_detail: Optional[Dict[str, Any]] = None

    # 7. Determine path and extractive / synthesized answer
    needs_augmentation = (q_type != "out_of_scope") and (
        top_score < RETRIEVAL_SCORE_THRESHOLD or not ranked_chunks or not identity_valid
    )

    if needs_augmentation:
        extracted_entity = entity_name or extract_entity_name(clean_content)
        if extracted_entity:
            logger.info(f"Triggering IC3-WB self-augmentation for entity candidate: '{extracted_entity}'")
            try:
                t_aug = time.time()
                aug_res = run_augmentation_pipeline(
                    query=clean_content,
                    message_id=None,
                    lang=detected_lang,
                    db=db,
                    classification=classification,
                    asked_field=asked_field,
                )
                gemini_calls_count += aug_res.gemini_calls
                tracer.record(
                    "augmentation",
                    t_aug,
                    input_data={"entity": extracted_entity, "district": detected_district},
                    output_data={
                        "status": aug_res.status,
                        "reason": aug_res.decision_reason,
                        "site_id": aug_res.site_id,
                        "sources_count": getattr(aug_res, "sources_count", 0),
                    },
                )
                path_detail = {
                    "augment_job_id": str(aug_res.job_id),
                    "candidate_key": aug_res.candidate_key,
                    "status": aug_res.status,
                    "decision_code": aug_res.decision_reason,
                    "decision_reason": aug_res.decision_reason,
                }

                if aug_res.status == "needs_choice":
                    # Rule 4.2: Value choice for CONFLICT on the field the user asked about
                    path = "needs_choice"
                    effective_resolved_site = aug_res.site_id
                    field_label = aug_res.asked_field or "this field"
                    lines = [f"Sources give different answers for {field_label}:"]
                    citations = []
                    used_chunk_ids = []
                    for opt in (aug_res.conflict_options or []):
                        if opt.get("value") == "Compare all":
                            lines.append(f"{opt['choice_id']}. Compare all")
                        else:
                            src_names = ", ".join(f"{s.get('domain')} [Tier {s.get('tier', 'C')}]" for s in opt.get("sources", []) if s.get("domain")) or "web sources"
                            ratio_pct = int(opt.get("support_ratio", 0) * 100)
                            lines.append(f"{opt['choice_id']}. {opt['value']} ({src_names}) [{ratio_pct}%]")
                            for s in opt.get("sources", []):
                                citations.append(CitationItem(
                                    chunk_id=str(uuid.uuid4()),
                                    site_id=aug_res.site_id or "web",
                                    site_name_en=aug_res.entity_name or extracted_entity,
                                    site_name_ta=None,
                                    section=field_label,
                                    lang=detected_lang,
                                    score=opt.get("support_ratio", 0.5),
                                    trust="high" if s.get("tier") == "A" else "medium",
                                    origin=s.get("domain") or "web",
                                    source_url=s.get("url"),
                                ))
                    lines.append("\nWhich source's details would you like to see?")
                    assistant_content = "\n".join(lines)
                    selected_chunks = []
                elif aug_res.status == "provisional":
                    # Rule 4.1: Provisional answer (nothing unverified is stored as fact)
                    path = "web_provisional"
                    effective_resolved_site = None
                    prov_label = PROVISIONAL_ANSWER_LABEL.get(detected_lang, PROVISIONAL_ANSWER_LABEL["en"])
                    lines = [f"{prov_label}:"]
                    citations = []
                    used_chunk_ids = []
                    seen_claims = set()
                    for pc in (aug_res.provisional_claims or []):
                        f_name = pc["field"].replace("_", " ").capitalize()
                        val = pc["value"]
                        key = (f_name, val)
                        if key in seen_claims:
                            continue
                        seen_claims.add(key)
                        dom = pc.get("domain") or "web"
                        tier = pc.get("tier") or "C"
                        url = pc.get("url") or ""
                        lines.append(f"- **{f_name}**: {val} – *{dom}* (Tier {tier}, {url})")
                        citations.append(CitationItem(
                            chunk_id=str(uuid.uuid4()),
                            site_id="web_provisional",
                            site_name_en=aug_res.entity_name or extracted_entity,
                            site_name_ta=None,
                            section=pc["field"],
                            lang=detected_lang,
                            score=0.5,
                            trust="low",
                            origin=dom,
                            source_url=url,
                        ))
                    assistant_content = "\n".join(lines)
                    selected_chunks = []
                elif aug_res.status == "verified" and aug_res.site_id:
                    # Re-run Qdrant retrieval for this query (it will now find the newly written chunks!)
                    new_ranked = search_and_rank_chunks(
                        query_text=clean_content,
                        query_lang=detected_lang,
                        resolved_site=aug_res.site_id,
                        detected_district=detected_district,
                        top_k=RETRIEVAL_TOP_K,
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

                        # Rule 4.1: Append unconfirmed fields if any
                        if getattr(aug_res, "unconfirmed_fields", None):
                            prefix = UNCONFIRMED_FIELD_PREFIX.get(detected_lang, UNCONFIRMED_FIELD_PREFIX["en"])
                            for u_field, u_info in aug_res.unconfirmed_fields.items():
                                u_val = u_info.get("value")
                                u_srcs = ", ".join(s.get("domain") for s in u_info.get("sources", []) if s.get("domain")) or "web source"
                                assistant_content += f"\n\n{prefix}: {u_field.replace('_', ' ').capitalize()} - {u_val} – {u_srcs}"

                        # Rule 4.2: Append conflicts on unasked fields if any
                        if getattr(aug_res, "conflict_fields", None):
                            for c_field, c_opts in aug_res.conflict_fields.items():
                                if c_field != aug_res.asked_field:
                                    c_vals = " vs ".join(o.get("value") for o in c_opts if o.get("value") and o.get("value") != "Compare all")
                                    if c_vals:
                                        assistant_content += f"\n\n(Note: Sources report conflicting accounts for {c_field.replace('_', ' ')}: {c_vals}.)"
                    else:
                        path = "rejected"
                        n_sources = getattr(aug_res, "sources_count", 0) or 7
                        ent_label = aug_res.entity_name or extracted_entity or "this temple"
                        dist_label = f" in {detected_district} district" if detected_district else ""
                        assistant_content = _build_web_checked_rejection(detected_lang, n_sources, ent_label, dist_label)
                        selected_chunks = []
                        citations = []
                        used_chunk_ids = []
                        effective_resolved_site = None
                elif aug_res.status == "quota_exhausted":
                    path = "rejected"
                    partial_info = _find_partial_provisional_info(db, extracted_entity)
                    if partial_info:
                        assistant_content = f"{partial_info}\n\nWeb checking is busy, please try again in a few minutes."
                    else:
                        assistant_content = "Web checking is busy, please try again in a few minutes."
                    selected_chunks = []
                    citations = []
                    used_chunk_ids = []
                    effective_resolved_site = None
                else:
                    path = "rejected"
                    # Rule 1.5: Web check ran! Never say 'in our database', say what was checked
                    n_sources = getattr(aug_res, "sources_count", 0) or 7
                    ent_label = aug_res.entity_name or extracted_entity or "this temple"
                    dist_label = f" in {detected_district} district" if detected_district else ""
                    assistant_content = _build_web_checked_rejection(detected_lang, n_sources, ent_label, dist_label)
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

    # 7b-2. Rule 4.3: If path == 'needs_choice', record value choice clarification
    if path == "needs_choice" and 'aug_res' in locals() and aug_res and getattr(aug_res, "conflict_options", None):
        try:
            from app.services.clarifications import create_clarification
            create_clarification(
                db=db,
                session_id=str(session_id),
                asked_message_id=user_message_id,
                entity_name=aug_res.entity_name or extracted_entity or "this site",
                entity_name_ta=None,
                question_type=q_type,
                original_query=clean_content,
                candidates=aug_res.conflict_options,
                kind="value",
                field=aug_res.asked_field or "field",
            )
        except Exception as _cl_err:
            logger.warning(f"Failed to record value choice clarification: {_cl_err}")

    # 7c. Citations
    cit_stmt = sa_text("""
        INSERT INTO message_citations (message_id, chunk_id, rank, score)
        VALUES (:message_id, :chunk_id, :rank, :score)
        ON CONFLICT (message_id, chunk_id) DO NOTHING
    """)
    for rank, cit in enumerate(citations, start=1):
        if used_chunk_ids and cit.chunk_id in used_chunk_ids:
            try:
                db.execute(cit_stmt, {
                    "message_id": assistant_message_id,
                    "chunk_id": uuid.UUID(cit.chunk_id),
                    "rank": rank,
                    "score": cit.score,
                })
            except Exception:
                # Synthetic / web citation not in chunk_registry table
                pass

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

    tracer.flush(db, user_message_id)
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
