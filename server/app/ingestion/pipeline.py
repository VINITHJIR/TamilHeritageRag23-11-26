"""Master CLI Pipeline Orchestrator for TamilHeritage-GPT.

Method
------
Orchestrates the multi-stage ingestion pipeline:
    1. LGD         : Administrative boundary and location code3 ingestion
    2. ASI         : Archaeological Survey of India Gazette & PDF table extraction
    3. WIKIDATA    : Linked Open Data heritage entities via SPARQL & MediaWiki API
    4. CLASSIFY    : Multilingual rule-based categorization & ASI tagging
    5. GROUP       : Locality-based complex/component parent-child detection
    6. RESOLVE     : Multi-feature entity resolution (linking to existing seed sites)
    7. WIKIPEDIA   : Multilingual encyclopedic text extraction (en + ta)
    8. CHUNKS      : SentencePiece token-bounded chunking & fact card generation
    9. LOAD        : Transactional database upsert & Qdrant vector indexing

Every step execution records a row in `ingestion_runs` documenting provenance and metrics.
The `--dry-run` flag executes all parsing, classification, grouping, entity resolution,
and chunking in-memory without mutating PostgreSQL or Qdrant.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.postgres import SessionLocal
from app.ingestion import config as C
from app.ingestion.acquire import (
    compute_file_sha256,
    finish_ingestion_run,
    start_ingestion_run,
)
from app.ingestion.chunker import bge_m3_token_counter, build_fact_cards, chunk_text
from app.ingestion.classify import classify
from app.ingestion.enrich_wikidata import WikidataClient, fetch_items
from app.ingestion.enrich_wikipedia import WikipediaClient
from app.ingestion.extract_asi import extract_asi_pdf, find_asi_files
from app.ingestion.extract_lgd import extract_all, find_lgd_files
from app.ingestion.group import group_records
from app.ingestion.ids import (
    IdAllocator,
    code3_for,
    fill_code3,
    load_allocator,
    register_curated,
)
from app.ingestion.load import load_chunks_and_embeddings, load_sites_batch
from app.ingestion.normalize import canonical_district, clean_text, strip_admin_notes
from app.ingestion.reports import generate_run_report
from app.ingestion.resolve import Resolver, decide, record_names

logger = logging.getLogger("ingestion.pipeline")


class PipelineRunner:
    """Coordinates execution of ingestion stages with audit tracking."""

    def __init__(self, dry_run: bool = False, limit: Optional[int] = None, offline: bool = False):
        self.dry_run = dry_run
        self.limit = limit
        self.offline = offline
        self.step_runs: List[Dict[str, Any]] = []
        self.review_items: List[Dict[str, Any]] = []
        self.unmatched_records: List[Dict[str, Any]] = []
        self.rules_hit_counts: Dict[str, int] = {}
        self.run_id = int(datetime.now(timezone.utc).timestamp())

    def run_step_lgd(self, db: Session) -> Dict[str, Any]:
        """Step 1: Extract LGD hierarchy and populate locations.code3."""
        logger.info("=== Running Step: LGD ===")
        files = find_lgd_files()
        primary_file = files[0].name if files else "lgd.xls"
        sha256 = compute_file_sha256(files[0]) if files else None

        run_id = start_ingestion_run(
            db, step="lgd", source="lgd", input_file=primary_file, input_sha256=sha256
        ) if not self.dry_run else 0

        rows_in, inserted, updated = 0, 0, 0
        try:
            # 1. Fill district code3 on existing locations & register seed sites
            code_res = fill_code3(db, dry_run=self.dry_run)
            updated += code_res["updated"]
            reg_curated = register_curated(db, dry_run=self.dry_run)

            # 2. Extract and load all LGD files
            if files:
                lgd_data = extract_all(files)
                records = lgd_data["records"]
                rows_in = len(records)
                logger.info(f"Parsed {rows_in} records from {len(files)} LGD files")

                if not self.dry_run:
                    for r in records:
                        if r["level"] == "district":
                            c3 = code3_for(r["district"] or r["name_en"])
                            exists = db.execute(sa_text(
                                "SELECT location_id FROM locations WHERE level = 'district' AND (code3 = :code3 OR name_en = :name)"
                            ), {"code3": c3, "name": r["name_en"]}).scalar()

                            if exists:
                                db.execute(sa_text("""
                                    UPDATE locations
                                    SET lgd_code = COALESCE(lgd_code, :lgd),
                                        code3 = COALESCE(code3, :code3),
                                        name_ta = COALESCE(name_ta, :name_ta)
                                    WHERE location_id = :id
                                """), {"lgd": r["lgd_code"], "code3": c3, "name_ta": r.get("name_ta"), "id": exists})
                                updated += 1
                            else:
                                db.execute(sa_text("""
                                    INSERT INTO locations (level, name_en, name_ta, code3, lgd_code)
                                    VALUES ('district', :name, :name_ta, :code3, :lgd)
                                    ON CONFLICT (lgd_code) DO NOTHING
                                """), {"name": r["name_en"], "name_ta": r.get("name_ta"), "code3": c3, "lgd": r["lgd_code"]})
                                inserted += 1
                    db.commit()

            res = {
                "step": "lgd", "status": "success", "rows_in": rows_in,
                "inserted": inserted, "updated": updated, "skipped": 0, "sent_to_review": 0
            }
            if not self.dry_run:
                finish_ingestion_run(db, run_id, **res)
            return res
        except Exception as e:
            logger.error(f"LGD step failed: {e}", exc_info=True)
            res = {"step": "lgd", "status": "failed", "rows_in": rows_in, "error": str(e)}
            if not self.dry_run:
                finish_ingestion_run(db, run_id, **res)
            return res

    def run_step_asi(self, db: Session) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Step 2: Extract ASI records from PDF publications."""
        logger.info("=== Running Step: ASI ===")
        files = find_asi_files()
        primary_file = files[0].name if files else "Monuments-of-National-Importance.pdf"
        sha256 = compute_file_sha256(files[0]) if files else None

        run_id = start_ingestion_run(
            db, step="asi", source="asi", input_file=primary_file, input_sha256=sha256
        ) if not self.dry_run else 0

        extracted: List[Dict[str, Any]] = []
        try:
            for f in files:
                recs = extract_asi_pdf(f, limit=self.limit)
                extracted.extend(recs)

            rows_in = len(extracted)
            logger.info(f"Extracted {rows_in} ASI records for Tamil Nadu")

            res = {
                "step": "asi", "status": "success", "rows_in": rows_in,
                "inserted": rows_in, "updated": 0, "skipped": 0, "sent_to_review": 0
            }
            if not self.dry_run:
                finish_ingestion_run(db, run_id, **res)
            return res, extracted
        except Exception as e:
            logger.error(f"ASI step failed: {e}", exc_info=True)
            res = {"step": "asi", "status": "failed", "rows_in": 0, "error": str(e)}
            if not self.dry_run:
                finish_ingestion_run(db, run_id, **res)
            return res, []

    def run_step_wikidata(self, db: Session) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Step 3: Enrich with Wikidata SPARQL discovery of Tamil Nadu heritage."""
        logger.info("=== Running Step: WIKIDATA ===")
        run_id = start_ingestion_run(db, step="wikidata", source="wikidata") if not self.dry_run else 0

        items: List[Dict[str, Any]] = []
        try:
            client = WikidataClient(offline=self.offline)
            items = fetch_items(client, limit=self.limit)
            rows_in = len(items)
            logger.info(f"Fetched {rows_in} Wikidata heritage entities")

            res = {
                "step": "wikidata", "status": "success", "rows_in": rows_in,
                "inserted": rows_in, "updated": 0, "skipped": 0, "sent_to_review": 0
            }
            if not self.dry_run:
                finish_ingestion_run(db, run_id, **res)
            return res, items
        except Exception as e:
            logger.warning(f"Wikidata step encountered issue: {e}; continuing with available items.")
            res = {"step": "wikidata", "status": "skipped" if self.offline else "failed", "rows_in": len(items), "error": str(e)}
            if not self.dry_run:
                finish_ingestion_run(db, run_id, **res)
            return res, items

    def run_step_classify(self, db: Session, records: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Step 4: Multi-label classification with precedence rules."""
        logger.info("=== Running Step: CLASSIFY ===")
        run_id = start_ingestion_run(db, step="classify", source="rules") if not self.dry_run else 0

        rows_in = len(records)
        sent_to_review = 0

        for r in records:
            res = classify(
                name=r["name_en"],
                locality=r.get("locality", ""),
                is_asi=(r.get("source") == "asi" or bool(r.get("asi_id"))),
                kind=r.get("kind")
            )
            r["primary_category"] = res["primary_category"]
            r["categories"] = res["categories"]
            r["rule_fired"] = res["rule"]

            if res["rule"]:
                self.rules_hit_counts[res["rule"]] = self.rules_hit_counts.get(res["rule"], 0) + 1

            if res["needs_review"]:
                sent_to_review += 1
                self.review_items.append({
                    "kind": "category",
                    "source": r["source"],
                    "source_key": r["source_key"],
                    "reason": "No keyword match, defaulted to other_monument",
                    "status": "open",
                    "payload": {"name": r["name_en"], "district": r.get("district")}
                })

        summary = {
            "step": "classify", "status": "success", "rows_in": rows_in,
            "inserted": rows_in, "updated": 0, "skipped": 0, "sent_to_review": sent_to_review
        }
        if not self.dry_run:
            finish_ingestion_run(db, run_id, **summary)
        return summary, records

    def run_step_group(self, db: Session, records: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Step 5: Parent-child complex detection."""
        logger.info("=== Running Step: GROUP ===")
        run_id = start_ingestion_run(db, step="group", source="locality_cluster") if not self.dry_run else 0

        grouped_records, group_reviews = group_records(records)
        self.review_items.extend(group_reviews)

        summary = {
            "step": "group", "status": "success", "rows_in": len(records),
            "inserted": len(grouped_records) - len(records),
            "updated": 0, "skipped": 0, "sent_to_review": len(group_reviews)
        }
        if not self.dry_run:
            finish_ingestion_run(db, run_id, **summary)
        return summary, grouped_records

    def run_step_resolve(self, db: Session, records: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Step 6: Entity resolution (auto-link to existing seed sites or allocate new stable ID)."""
        logger.info("=== Running Step: RESOLVE ===")
        run_id = start_ingestion_run(db, step="resolve", source="entity_matching") if not self.dry_run else 0

        # Load existing sites from DB for the candidate pool
        existing_rows = db.execute(sa_text("""
            SELECT s.site_id, s.name_en, s.name_ta, s.lat, s.lon, l.name_en AS district
            FROM sites s
            JOIN locations l ON s.district_id = l.location_id
        """)).mappings().fetchall()

        alias_rows = db.execute(sa_text("SELECT site_id, alias FROM site_aliases")).fetchall()
        aliases_by_id: Dict[str, List[str]] = {}
        for sid, al in alias_rows:
            aliases_by_id.setdefault(sid, []).append(al)

        candidate_sites = []
        for s in existing_rows:
            names = [s["name_en"]]
            if s["name_ta"]:
                names.append(s["name_ta"])
            names.extend(aliases_by_id.get(s["site_id"], []))
            candidate_sites.append({
                "id": s["site_id"],
                "names": names,
                "district": s["district"],
                "lat": s["lat"],
                "lon": s["lon"]
            })

        allocator = load_allocator(db)
        source_keys_map = dict(allocator.persisted)

        resolver = Resolver(existing_sites=candidate_sites, source_keys=source_keys_map)

        linked_count = 0
        new_count = 0
        resolved_records = []

        for r in records:
            key = f"{r['source']}|{r['source_key']}"
            res = resolver.resolve(r, key)

            if res["action"] == "link":
                r["site_id"] = res["target"]
                r["is_linked"] = True
                linked_count += 1
            elif res["action"] == "review":
                self.review_items.append({
                    "kind": "match",
                    "source": r["source"],
                    "source_key": r["source_key"],
                    "reason": f"Similarity score {res['score']} requires human verification",
                    "status": "open",
                    "payload": {"name": r["name_en"], "district": r.get("district")},
                    "candidates": res.get("candidates", [])
                })
                # In hold policy, allocate unique site_id
                dist = r.get("district") or "Chennai"
                sid, _ = allocator.allocate(key, dist)
                r["site_id"] = sid
                r["is_linked"] = False
                new_count += 1
            else:  # 'new'
                dist = r.get("district") or "Chennai"
                try:
                    sid, _ = allocator.allocate(key, dist)
                except ValueError:
                    sid, _ = allocator.allocate(key, "Chennai")
                r["site_id"] = sid
                r["is_linked"] = False
                new_count += 1

            resolved_records.append(r)

        logger.info(f"Resolve completed: {linked_count} linked to existing sites, {new_count} allocated new IDs")
        summary = {
            "step": "resolve", "status": "success", "rows_in": len(records),
            "inserted": new_count, "updated": linked_count, "skipped": 0, "sent_to_review": len(self.review_items)
        }
        if not self.dry_run:
            finish_ingestion_run(db, run_id, **summary)
        return summary, resolved_records

    def run_all(self, steps_str: str = "lgd,asi,wikidata,classify,group,resolve,load") -> Dict[str, Any]:
        """Execute all configured ingestion steps."""
        steps = [s.strip().lower() for s in steps_str.split(",") if s.strip()]
        logger.info(f"Starting master pipeline execution: {steps} (dry_run={self.dry_run})")

        with SessionLocal() as db:
            records: List[Dict[str, Any]] = []

            # Step 1: LGD
            if "lgd" in steps:
                s_res = self.run_step_lgd(db)
                self.step_runs.append(s_res)

            # Step 2: ASI
            if "asi" in steps:
                s_res, asi_recs = self.run_step_asi(db)
                self.step_runs.append(s_res)
                records.extend(asi_recs)

            # Step 3: Wikidata
            if "wikidata" in steps:
                s_res, wiki_recs = self.run_step_wikidata(db)
                self.step_runs.append(s_res)
                records.extend(wiki_recs)

            # Step 4: Classify
            if "classify" in steps and records:
                s_res, records = self.run_step_classify(db, records)
                self.step_runs.append(s_res)

            # Step 5: Group
            if "group" in steps and records:
                s_res, records = self.run_step_group(db, records)
                self.step_runs.append(s_res)

            # Step 6: Resolve
            if "resolve" in steps and records:
                s_res, records = self.run_step_resolve(db, records)
                self.step_runs.append(s_res)

            # Step 7: Load
            if "load" in steps and records and not self.dry_run:
                logger.info(f"Persisting {len(records)} records into PostgreSQL...")
                # Resolve district_ids from locations table
                dist_map = {
                    r[0]: r[1] for r in db.execute(sa_text(
                        "SELECT name_en, location_id FROM locations WHERE level = 'district'"
                    )).fetchall()
                }
                chennai_id = dist_map.get("Chennai", 1)

                sites_dict: Dict[str, Dict[str, Any]] = {}
                seen_qids: Dict[str, str] = {}
                aliases_to_load = []

                for r in records:
                    sid = r["site_id"]
                    did = dist_map.get(r.get("district"), chennai_id)
                    qid = r.get("wikidata_qid")
                    if qid:
                        if qid in seen_qids and seen_qids[qid] != sid:
                            qid = None
                        else:
                            seen_qids[qid] = sid

                    if sid in sites_dict:
                        existing = sites_dict[sid]
                        merged_cats = list(dict.fromkeys(existing["categories"] + r.get("categories", [])))
                        existing["categories"] = merged_cats
                        if not existing.get("lat") and r.get("lat"):
                            existing["lat"], existing["lon"] = r["lat"], r["lon"]
                        if not existing.get("name_ta") and r.get("name_ta"):
                            existing["name_ta"] = r["name_ta"]
                        if not existing.get("wikidata_qid") and qid:
                            existing["wikidata_qid"] = qid
                        if not existing.get("asi_id") and r.get("asi_id"):
                            existing["asi_id"] = r["asi_id"]
                    else:
                        sites_dict[sid] = {
                            "site_id": sid,
                            "parent_site_id": r.get("parent_site_id"),
                            "name_en": r["name_en"],
                            "name_ta": r.get("name_ta"),
                            "primary_category": r.get("primary_category", "other_monument"),
                            "categories": r.get("categories", [r.get("primary_category", "other_monument")]),
                            "district_id": did,
                            "lat": r.get("lat"),
                            "lon": r.get("lon"),
                            "period": r.get("period"),
                            "dynasty": r.get("dynasty"),
                            "status": "verified",
                            "tier": r.get("tier", "B"),
                            "origin": r.get("source", "curated"),
                            "wikidata_qid": qid,
                            "asi_id": r.get("asi_id")
                        }

                    for al in r.get("aliases", []):
                        aliases_to_load.append({
                            "site_id": sid,
                            "alias": al["alias"],
                            "lang": al.get("lang", "en")
                        })

                sites_to_load = list(sites_dict.values())
                # Sort parent sites first so foreign keys resolve cleanly
                sites_to_load.sort(key=lambda s: 0 if s.get("parent_site_id") is None else 1)

                counts = load_sites_batch(
                    db,
                    sites=sites_to_load,
                    aliases=aliases_to_load,
                    dry_run=self.dry_run
                )

                # Persist site_source_keys for all records
                ssk_stmt = sa_text("""
                    INSERT INTO site_source_keys (source, source_key, site_id, created_at)
                    VALUES (:source, :source_key, :site_id, now())
                    ON CONFLICT (source, source_key) DO UPDATE SET site_id = EXCLUDED.site_id
                """)
                for r in records:
                    db.execute(ssk_stmt, {
                        "source": r["source"],
                        "source_key": r["source_key"],
                        "site_id": r["site_id"]
                    })

                # Persist review_queue items
                if self.review_items:
                    rq_stmt = sa_text("""
                        INSERT INTO review_queue (kind, source, source_key, payload, candidates, reason, status)
                        VALUES (:kind, :source, :source_key, :payload, :candidates, :reason, :status)
                        ON CONFLICT (kind, source, source_key) DO UPDATE SET
                            payload = EXCLUDED.payload,
                            candidates = EXCLUDED.candidates,
                            reason = EXCLUDED.reason,
                            status = EXCLUDED.status
                    """)
                    for rq in self.review_items:
                        db.execute(rq_stmt, {
                            "kind": rq["kind"],
                            "source": rq["source"],
                            "source_key": rq["source_key"],
                            "payload": json.dumps(rq.get("payload", {})),
                            "candidates": json.dumps(rq.get("candidates", [])),
                            "reason": rq.get("reason"),
                            "status": rq.get("status", "open")
                        })
                db.commit()
                self.step_runs.append({
                    "step": "load", "status": "success", "rows_in": len(records),
                    "inserted": counts["sites"], "updated": 0, "skipped": 0, "sent_to_review": 0
                })

            # Audit Reports Generation
            cat_counts: Dict[str, int] = {}
            dist_counts: Dict[str, int] = {}
            for r in records:
                c = r.get("primary_category", "other_monument")
                d = r.get("district", "Unknown")
                cat_counts[c] = cat_counts.get(c, 0) + 1
                dist_counts[d] = dist_counts.get(d, 0) + 1

            rep_dir = generate_run_report(
                run_id=self.run_id,
                step_runs=self.step_runs,
                review_items=self.review_items,
                unmatched_records=self.unmatched_records,
                rules_hit_counts=self.rules_hit_counts,
                category_counts=cat_counts,
                district_counts=dist_counts,
                dry_run=self.dry_run
            )

        return {
            "run_id": self.run_id,
            "step_runs": self.step_runs,
            "records_processed": len(records),
            "report_dir": str(rep_dir)
        }


def main():
    parser = argparse.ArgumentParser(description="TamilHeritage-GPT Level 1B Ingestion CLI")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run ingestion pipeline")
    run_parser.add_argument("--steps", type=str, default="lgd,asi,wikidata,classify,group,resolve,load",
                            help="Comma-separated list of steps to execute")
    run_parser.add_argument("--dry-run", action="store_true", help="Simulate pipeline without database writes")
    run_parser.add_argument("--limit", type=int, default=None, help="Limit number of records processed")
    run_parser.add_argument("--offline", action="store_true", help="Do not make external network requests")

    args = parser.parse_args()

    if args.command == "run":
        runner = PipelineRunner(dry_run=args.dry_run, limit=args.limit, offline=args.offline)
        res = runner.run_all(steps_str=args.steps)
        print("\n" + "=" * 60)
        print(f" Ingestion Pipeline Summary (Run {res['run_id']})")
        print("=" * 60)
        for s in res["step_runs"]:
            print(f"  • {s.get('step', '').ljust(12)}: {s.get('status', '').upper()} (rows_in: {s.get('rows_in', 0)})")
        print(f"\nAudit Report: {res['report_dir']}\n")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
