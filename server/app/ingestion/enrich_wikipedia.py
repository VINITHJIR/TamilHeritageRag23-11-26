"""Wikipedia enrichment (English + Tamil), licensed CC BY-SA 4.0.

Method
------
1. Titles come from Wikidata sitelinks (enwiki / tawiki) of linked or new sites, so no
   free-text page search is performed (no risk of fetching the wrong article).
2. Text: MediaWiki API ``action=query&prop=extracts|info|revisions&explaintext=1
   &exsectionformat=wiki&redirects=1`` -> plain text with "== Heading ==" markers, the
   canonical URL, and the revision id (pinned in the source record).
3. Sections kept: the lead (-> section 'overview'), and level-2 sections whose heading
   matches SECTION_MAP (English "History", "Architecture", ...; Tamil "வரலாறு",
   "கட்டடக்கலை", ...). Sub-sections are included in their parent section. Reference-like
   sections (References, See also, Gallery, ...) are never used.
4. Each page becomes one ``sources`` row: tier 'B', url, fetched_at, content_hash =
   sha256(extract), license "CC BY-SA 4.0, Wikipedia contributors".
5. Responses are cached in server/data/cache/wikipedia/; >= 1 s between requests.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.ingestion import config as C
from app.ingestion.common import CachedHttp, sha256_text
from app.ingestion.normalize import clean_text

logger = logging.getLogger(__name__)

SECTION_MAP = {
    "en": [(r"^(history|historical background|early history|legend(s)?( and history)?)$", "history"),
           (r"^(architecture|description|layout|structure|the temple|temple complex)$", "architecture")],
    "ta": [(r"^(வரலாறு|தல வரலாறு|வரலாற்றுப் பின்னணி|தொன்மம்)$", "history"),
           (r"^(கட்டடக்கலை|கட்டிடக்கலை|கட்டடக் கலை|கட்டிடக் கலை|அமைப்பு|கோயில் அமைப்பு)$", "architecture")],
}
_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$", re.M)


def split_sections(extract: str) -> dict[str, str]:
    """Return {'lead': ..., '<level-2 heading>': text including its sub-sections}."""
    out: dict[str, str] = {}
    matches = list(_HEADING.finditer(extract))
    out["lead"] = extract[: matches[0].start()].strip() if matches else extract.strip()
    current, buf_start = None, None
    for i, m in enumerate(matches):
        level = len(m.group(1))
        if level == 2:
            if current is not None:
                out[current] = _strip_headings(extract[buf_start:m.start()])
            current, buf_start = clean_text(m.group(2)), m.end()
    if current is not None:
        out[current] = _strip_headings(extract[buf_start:])
    return out


def _strip_headings(text: str) -> str:
    return _HEADING.sub(lambda m: "\n" + clean_text(m.group(2)) + "\n", text).strip()


def select_sections(sections: dict[str, str], lang: str) -> list[tuple[str, str]]:
    picked: list[tuple[str, str]] = []
    if sections.get("lead"):
        picked.append(("overview", sections["lead"]))
    used = set()
    for heading, body in sections.items():
        if heading == "lead" or not body:
            continue
        for rx, sec in SECTION_MAP.get(lang, []):
            if sec not in used and re.match(rx, heading.strip().lower()):
                picked.append((sec, body))
                used.add(sec)
                break
    return picked


class WikipediaClient:
    def __init__(self, offline: bool = False):
        self.http = CachedHttp("wikipedia", offline=offline)

    def page(self, lang: str, title: str) -> Optional[dict]:
        body = self.http.get_json(f"https://{lang}.wikipedia.org/w/api.php", {
            "action": "query", "prop": "extracts|info|revisions", "explaintext": 1,
            "exsectionformat": "wiki", "redirects": 1, "inprop": "url", "rvprop": "ids",
            "titles": title, "format": "json", "formatversion": 2})
        pages = body.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing") or not pages[0].get("extract"):
            return None
        p = pages[0]
        extract = p["extract"]
        return {
            "lang": lang, "title": p["title"], "url": p.get("fullurl") or
            f"https://{lang}.wikipedia.org/wiki/{p['title'].replace(' ', '_')}",
            "revid": (p.get("revisions") or [{}])[0].get("revid"),
            "content_hash": sha256_text(extract), "license": C.WIKIPEDIA_LICENSE,
            "sections": select_sections(split_sections(extract), lang),
        }
