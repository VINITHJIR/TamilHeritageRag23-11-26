"""Token-bounded chunking and structured fact cards.

Method (text chunks)
--------------------
Token counts use the BAAI/bge-m3 tokenizer (XLM-RoBERTa SentencePiece; loaded alone via
``transformers.AutoTokenizer``, the 2.3 GB model is not needed), so limits are exact for
the embedding model.

1. Paragraphs are split on blank lines; each paragraph is split into sentences on
   . ! ? and the Indic danda (। ॥) when followed by whitespace. A full stop does NOT end
   a sentence after a known abbreviation (St., Dr., No., c., A.D., ...; Tamil கி.பி.,
   கி.மு., பொ.ஊ.) or after a word of <= 2 letters (initials such as "T. S."), which also
   protects Tamil abbreviations like "கி." in "கி.பி. 1010".
2. Sentences are packed greedily into chunks of <= max_tokens (350). A chunk is closed
   early at a paragraph boundary once it is >= 60 % full, so chunks prefer paragraph edges.
3. Overlap: each new chunk starts with the trailing sentences of the previous chunk whose
   total is <= overlap_tokens (50) (whole sentences only, never mid-sentence).
4. A single sentence longer than max_tokens is split on whitespace into <= max_tokens
   pieces (no overlap inside such a sentence).
5. Invariant (tested): every chunk has <= max_tokens tokens and no text is dropped.

Method (fact cards)
-------------------
``build_fact_cards`` turns a site's DATABASE row (plus its facts, aliases, parent and
children read from PostgreSQL) into one structured chunk per language, section
'overview_facts': English always, Tamil when the site has a Tamil name. Only fields that
are present are written; nothing is inferred or invented.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Callable, Optional

from app.ingestion.config import ChunkConfig

TokenCounter = Callable[[str], int]

ABBREVIATIONS = {
    "st", "dr", "mr", "mrs", "ms", "no", "nos", "c", "ca", "cf", "vs", "etc", "e.g", "i.e", "a.d", "b.c",
    "lt", "capt", "col", "gen", "rs", "sri", "smt", "prof", "fig", "vol", "ed", "approx", "est", "jr", "sr",
    "கி.பி", "கி.மு", "பொ.ஊ", "பொ.ஊ.மு", "திரு",
}
_SENT_END = re.compile(r"(?<=[.!?।॥])[\"'”’)\]]*\s+")


@lru_cache(maxsize=1)
def bge_m3_token_counter() -> TokenCounter:
    from transformers import AutoTokenizer
    from app.core.config import settings
    tok = AutoTokenizer.from_pretrained(settings.EMBED_MODEL)
    return lambda text: len(tok.encode(text, add_special_tokens=False))


def split_sentences(paragraph: str) -> list[str]:
    out, start = [], 0
    for m in _SENT_END.finditer(paragraph):
        before = paragraph[start:m.start()].rstrip()
        word = re.split(r"\s", before)[-1] if before else ""
        word_core = word.rstrip(".!?।॥\"'”’)]").lower()
        if before.endswith(".") and (word_core in ABBREVIATIONS or len(word_core.replace(".", "")) <= 2):
            continue
        sent = paragraph[start:m.start()].strip()
        if sent:
            out.append(sent)
        start = m.end()
    tail = paragraph[start:].strip()
    if tail:
        out.append(tail)
    return out


def _hard_split(sentence: str, count: TokenCounter, max_tokens: int) -> list[str]:
    pieces, cur = [], []
    for w in sentence.split():
        if cur and count(" ".join(cur + [w])) > max_tokens:
            pieces.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        pieces.append(" ".join(cur))
    return pieces


def chunk_text(text: str, count: TokenCounter, cfg: ChunkConfig = ChunkConfig()) -> list[str]:
    """Split ``text`` into chunks of <= cfg.max_tokens with sentence-level overlap."""
    units: list[tuple[str, int, int]] = []        # (sentence, tokens, paragraph_no)
    for pno, para in enumerate(p for p in re.split(r"\n\s*\n", text) if p.strip()):
        para = " ".join(para.split())
        for s in split_sentences(para):
            n = count(s)
            if n <= cfg.max_tokens:
                units.append((s, n, pno))
            else:
                units.extend((piece, count(piece), pno) for piece in _hard_split(s, count, cfg.max_tokens))

    def render(us):
        parts, last_p = [], None
        for s, _, p in us:
            parts.append(("\n\n" if last_p is not None and p != last_p else " ") + s if parts else s)
            last_p = p
        return "".join(parts)

    chunks: list[str] = []
    cur: list[tuple[str, int, int]] = []
    fresh = 0                                     # units in cur that are not overlap
    i = 0
    while i < len(units):
        u = units[i]
        candidate = cur + [u]
        too_big = count(render(candidate)) > cfg.max_tokens
        para_break = cur and u[2] != cur[-1][2] and fresh and count(render(cur)) >= 0.6 * cfg.max_tokens
        if cur and fresh and (too_big or para_break):
            chunks.append(render(cur))
            tail, t = [], 0
            for s in reversed(cur):
                if t + s[1] > cfg.overlap_tokens:
                    break
                tail.insert(0, s)
                t += s[1]
            cur, fresh = tail, 0
            if count(render(cur + [u])) > cfg.max_tokens:   # overlap would overflow
                cur = []
            continue
        cur, fresh, i = candidate, fresh + 1, i + 1
    if cur and fresh:
        chunks.append(render(cur))
    return chunks


# ----------------------------------------------------------------- fact cards
def _fmt_coord(lat, lon) -> Optional[str]:
    if lat is None or lon is None:
        return None
    return f"{float(lat):.5f}, {float(lon):.5f}"


def build_fact_cards(site: dict) -> list[tuple[str, str]]:
    """site keys (from DB): name_en, name_ta, primary_label_en/_ta, category_labels_en/_ta,
    district_en/_ta, subdistrict_en/_ta, local_body_en/_ta, lat, lon, period, dynasty,
    categories, asi_id, wikidata_qid, parent_name_en/_ta, children_en, aliases[{alias,lang}],
    facts[{field,value,lang}]. Returns [(lang, text)]."""
    cards: list[tuple[str, str]] = []
    asi = "asi_protected" in (site.get("categories") or [])
    aliases_en = [a["alias"] for a in site.get("aliases", []) if a["lang"] in ("en", "ta-Latn")
                  and a["alias"] != site["name_en"]]
    facts_en = [f for f in site.get("facts", []) if f["lang"] == "en"]

    en = [f"{site['name_en']}"]
    if site.get("name_ta"):
        en.append(f"Tamil name: {site['name_ta']}")
    if aliases_en:
        en.append("Also known as: " + "; ".join(aliases_en[:10]))
    if site.get("primary_label_en"):
        en.append(f"Type: {site['primary_label_en']}")
    if site.get("category_labels_en"):
        en.append("Categories: " + ", ".join(site["category_labels_en"]))
    loc = [x for x in (site.get("local_body_en"), site.get("subdistrict_en")) if x]
    if site.get("district_en"):
        loc.append(f"{site['district_en']} district")
    if loc:
        en.append("Location: " + ", ".join(loc) + ", Tamil Nadu")
    if _fmt_coord(site.get("lat"), site.get("lon")):
        en.append(f"Coordinates: {_fmt_coord(site['lat'], site['lon'])}")
    if site.get("period"):
        en.append(f"Period: {site['period']}")
    if site.get("dynasty"):
        en.append(f"Dynasty: {site['dynasty']}")
    for f in facts_en:
        if f["field"] == "asi_monument_id":
            continue
        en.append(f"{f['field'].replace('_', ' ').capitalize()}: {f['value']}")
    if asi:
        en.append("Protection: Centrally protected monument of national importance (Archaeological Survey of India)"
                  + (f", ASI id {site['asi_id']}" if site.get("asi_id") else ""))
    if site.get("parent_name_en"):
        en.append(f"Part of: {site['parent_name_en']}")
    if site.get("children_en"):
        kids = site["children_en"]
        en.append("Components: " + "; ".join(kids[:15]) + (f" (and {len(kids) - 15} more)" if len(kids) > 15 else ""))
    if site.get("wikidata_qid"):
        en.append(f"Wikidata: {site['wikidata_qid']}")
    cards.append(("en", "\n".join(en)))

    if site.get("name_ta"):
        ta = [site["name_ta"], f"ஆங்கிலப் பெயர்: {site['name_en']}"]
        aliases_ta = [a["alias"] for a in site.get("aliases", []) if a["lang"] == "ta" and a["alias"] != site["name_ta"]]
        if aliases_ta:
            ta.append("வேறு பெயர்கள்: " + "; ".join(aliases_ta[:10]))
        if site.get("primary_label_ta"):
            ta.append(f"வகை: {site['primary_label_ta']}")
        if site.get("category_labels_ta"):
            ta.append("பிரிவுகள்: " + ", ".join(site["category_labels_ta"]))
        loc_ta = [x for x in (site.get("local_body_ta"), site.get("subdistrict_ta")) if x]
        if site.get("district_ta"):
            loc_ta.append(f"{site['district_ta']} மாவட்டம்")
        if loc_ta:
            ta.append("அமைவிடம்: " + ", ".join(loc_ta) + ", தமிழ்நாடு")
        if _fmt_coord(site.get("lat"), site.get("lon")):
            ta.append(f"புவி ஆள்கூறுகள்: {_fmt_coord(site['lat'], site['lon'])}")
        for f in site.get("facts", []):
            if f["lang"] == "ta":
                ta.append(f"{f['field']}: {f['value']}")
        if asi:
            ta.append("பாதுகாப்பு: இந்தியத் தொல்லியல் துறையால் பாதுகாக்கப்படும் தேசிய முக்கியத்துவம் வாய்ந்த நினைவுச்சின்னம்")
        if site.get("parent_name_ta") or site.get("parent_name_en"):
            ta.append(f"இதன் பகுதி: {site.get('parent_name_ta') or site['parent_name_en']}")
        cards.append(("ta", "\n".join(ta)))
    return cards
