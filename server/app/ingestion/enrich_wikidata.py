"""Wikidata enrichment (CC0).

Method
------
1. Property check. Before anything else the label of WIKIDATA_ASI_PROPERTY (P1371) is
   fetched and must contain "ASI"; otherwise the step aborts. This guards against a
   wrong property id silently importing unrelated identifiers.
2. Item discovery (SPARQL, https://query.wikidata.org/sparql). Items located in Tamil
   Nadu (wdt:P131* wd:Q1445) that EITHER carry an ASI monument ID (P1371) OR a heritage
   designation (P1435). The containing district (instance of Q1149652 "district of
   India") is returned for district matching.
3. Item details via the MediaWiki API ``wbgetentities`` (batches of 50): labels and
   aliases in en/ta/kn, P625 coordinates, P1371 ASI id, P571 inception, P31 instance-of,
   enwiki/tawiki sitelinks. Instance-of labels are fetched the same way and fed to the
   classifier.
4. Curated sites without a QID: ``wbsearchentities`` on their English name (top 5);
   candidates are scored by resolve.py like any other record (name + district + geo).
5. Linking. Items are resolved with resolve.Resolver against existing sites and the
   entities produced by the resolve step. 'link' -> enrich that target (QID, coords if
   missing, aliases, sitelinks, facts). Unmatched items that carry P1371 are ASI-listed
   monuments and become new sites (tier B); other unmatched items are reported only.
6. Politeness and reproducibility: descriptive User-Agent, >= 1 s between requests,
   back-off on 429/5xx, and every response cached in server/data/cache/wikidata/.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.ingestion import config as C
from app.ingestion.common import CachedHttp, batched
from app.ingestion.normalize import canonical_district, clean_text

logger = logging.getLogger(__name__)

SPARQL_URL = "https://query.wikidata.org/sparql"
API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_ASI_PROPERTY = "P1371"
TAMIL_NADU_QID = "Q1445"
DISTRICT_OF_INDIA_QID = "Q1149652"
LANGS = ("en", "ta", "kn")

DISCOVERY_QUERY = f"""
SELECT ?item (SAMPLE(?asi) AS ?asi_id) (SAMPLE(?distLabel) AS ?district) WHERE {{
  {{ ?item wdt:{WIKIDATA_ASI_PROPERTY} ?asi . }} UNION {{ ?item wdt:P1435 ?designation . }}
  ?item wdt:P131* wd:{TAMIL_NADU_QID} .
  OPTIONAL {{
    ?item wdt:P131* ?dist . ?dist wdt:P31 wd:{DISTRICT_OF_INDIA_QID} .
    ?dist rdfs:label ?distLabel . FILTER(LANG(?distLabel) = "en")
  }}
}} GROUP BY ?item
"""


class WikidataClient:
    def __init__(self, offline: bool = False):
        self.http = CachedHttp("wikidata", offline=offline)

    def verify_asi_property(self) -> str:
        body = self.http.get_json(API_URL, {"action": "wbgetentities", "ids": WIKIDATA_ASI_PROPERTY,
                                            "props": "labels|descriptions", "languages": "en", "format": "json"})
        ent = body["entities"][WIKIDATA_ASI_PROPERTY]
        label = ent.get("labels", {}).get("en", {}).get("value", "")
        desc = ent.get("descriptions", {}).get("en", {}).get("value", "")
        if "asi" not in label.lower() and "archaeological survey" not in (label + desc).lower():
            raise RuntimeError(f"{WIKIDATA_ASI_PROPERTY} label is {label!r}, not the ASI monument ID; aborting")
        logger.info("Wikidata: %s = %r (%s)", WIKIDATA_ASI_PROPERTY, label, desc)
        return label

    def discover(self) -> list[dict]:
        body = self.http.get_json(SPARQL_URL, {"query": DISCOVERY_QUERY, "format": "json"},
                                  headers={"Accept": "application/sparql-results+json"})
        out = []
        for b in body["results"]["bindings"]:
            qid = b["item"]["value"].rsplit("/", 1)[-1]
            out.append({"qid": qid, "asi_id": b.get("asi_id", {}).get("value"),
                        "district_label": b.get("district", {}).get("value")})
        return sorted(out, key=lambda x: int(x["qid"][1:]))

    def entities(self, qids: list[str]) -> dict[str, dict]:
        res: dict[str, dict] = {}
        for chunk in batched(sorted(set(qids)), 50):
            body = self.http.get_json(API_URL, {
                "action": "wbgetentities", "ids": "|".join(chunk), "format": "json",
                "props": "labels|aliases|claims|sitelinks|descriptions",
                "languages": "|".join(LANGS), "sitefilter": "enwiki|tawiki"})
            res.update(body.get("entities", {}))
        return res

    def labels(self, qids: list[str]) -> dict[str, str]:
        res: dict[str, str] = {}
        for chunk in batched(sorted(set(qids)), 50):
            body = self.http.get_json(API_URL, {"action": "wbgetentities", "ids": "|".join(chunk),
                                                "props": "labels", "languages": "en", "format": "json"})
            for q, e in body.get("entities", {}).items():
                res[q] = e.get("labels", {}).get("en", {}).get("value", q)
        return res

    def search(self, name: str, limit: int = 5) -> list[str]:
        body = self.http.get_json(API_URL, {"action": "wbsearchentities", "search": name, "language": "en",
                                            "type": "item", "limit": limit, "format": "json"})
        return [h["id"] for h in body.get("search", [])]


def _claim_values(ent: dict, prop: str) -> list:
    vals = []
    for c in ent.get("claims", {}).get(prop, []):
        dv = c.get("mainsnak", {}).get("datavalue")
        if dv:
            vals.append(dv["value"])
    return vals


def _inception(ent: dict) -> Optional[str]:
    for v in _claim_values(ent, "P571"):
        t, prec = v.get("time", ""), v.get("precision", 9)
        m = re.match(r"([+-])0*(\d+)-", t)
        if not m:
            continue
        year = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        if prec <= 7:                       # century or coarser
            c = (abs(year) - 1) // 100 + 1
            return f"{c}th century {'BCE' if year < 0 else 'CE'}"
        return f"{abs(year)} {'BCE' if year < 0 else 'CE'}"
    return None


def entity_to_record(qid: str, ent: dict, hint: dict, p31_labels: dict[str, str]) -> dict:
    labels = {l: ent.get("labels", {}).get(l, {}).get("value") for l in LANGS}
    aliases = [{"alias": clean_text(a["value"]), "lang": l}
               for l in LANGS for a in ent.get("aliases", {}).get(l, [])]
    for l in ("ta", "kn"):
        if labels[l]:
            aliases.append({"alias": clean_text(labels[l]), "lang": l})
    coords = _claim_values(ent, "P625")
    asi_ids = [str(v) for v in _claim_values(ent, WIKIDATA_ASI_PROPERTY)]
    p31 = [v["id"] for v in _claim_values(ent, "P31") if isinstance(v, dict) and "id" in v]
    sl = ent.get("sitelinks", {})
    facts = []
    inc = _inception(ent)
    if inc:
        facts.append({"field": "inception", "value": inc, "lang": "en"})
    for a in asi_ids:
        facts.append({"field": "asi_monument_id", "value": a, "lang": "en"})
    return {
        "source": "wikidata", "source_key": qid, "wikidata_qid": qid, "tier": "B",
        "name_en": clean_text(labels["en"] or labels["ta"] or qid),
        "name_ta": clean_text(labels["ta"]) or None,
        "aliases": aliases,
        "description": ent.get("descriptions", {}).get("en", {}).get("value"),
        "lat": coords[0]["latitude"] if coords else None,
        "lon": coords[0]["longitude"] if coords else None,
        "asi_id": asi_ids[0] if asi_ids else hint.get("asi_id"),
        "district": canonical_district(hint.get("district_label")),
        "district_raw": hint.get("district_label"),
        "instance_of": [p31_labels.get(q, q) for q in p31],
        "enwiki": sl.get("enwiki", {}).get("title"), "tawiki": sl.get("tawiki", {}).get("title"),
        "facts": facts, "source_url": f"https://www.wikidata.org/wiki/{qid}",
    }


def fetch_items(client: WikidataClient, extra_qids: list[str] = (), limit: Optional[int] = None) -> list[dict]:
    client.verify_asi_property()
    hints = {h["qid"]: h for h in client.discover()}
    logger.info("Wikidata: discovered %d Tamil Nadu heritage items", len(hints))
    qids = sorted(hints, key=lambda q: int(q[1:]))
    if limit:
        qids = qids[:limit]
    qids = list(dict.fromkeys(qids + list(extra_qids)))
    ents = client.entities(qids)
    p31 = sorted({v["id"] for e in ents.values() for v in _claim_values(e, "P31") if isinstance(v, dict)})
    p31_labels = client.labels(p31) if p31 else {}
    return [entity_to_record(q, ents[q], hints.get(q, {}), p31_labels) for q in qids if q in ents and "missing" not in ents[q]]
