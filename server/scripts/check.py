"""Consistency and row count checker for TamilHeritage-GPT.

Prints row counts per table, Qdrant point count, and verifies consistency
between PostgreSQL chunk_registry and Qdrant points.
"""

import logging
import sys
from pathlib import Path

# Ensure server directory is in sys.path before any local imports
server_root = Path(__file__).resolve().parent.parent
if str(server_root) not in sys.path:
    sys.path.insert(0, str(server_root))

from sqlalchemy import text as sa_text
from app.core.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import get_qdrant_client

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("check")

TABLES_TO_CHECK = [
    "locations",
    "categories",
    "sites",
    "site_aliases",
    "sources",
    "site_facts",
    "chunk_registry",
    "question_types",
    "users",
    "chat_sessions",
    "chat_messages",
    "message_citations",
    "message_feedback",
    "query_logs",
    # Level 1B (002_level1b.sql)
    "site_source_keys",
    "ingestion_runs",
    "review_queue",
]


def main():
    print("=======================================================")
    print(" TamilHeritage-GPT: System Health & Consistency Check")
    print("=======================================================\n")

    # 1. PostgreSQL Row Counts
    print("--- 1. PostgreSQL Table Row Counts (Heritage_DBJournal) ---")
    pg_counts = {}
    with SessionLocal() as db:
        for tbl in TABLES_TO_CHECK:
            try:
                count = db.execute(sa_text(f"SELECT count(*) FROM {tbl}")).scalar()
                pg_counts[tbl] = count
                print(f"  • {tbl.ljust(22)}: {count}")
            except Exception as e:
                pg_counts[tbl] = f"ERROR: {e}"
                print(f"  • {tbl.ljust(22)}: [ERROR: {e}]")

        # 2. Qdrant Points Count
        print("\n--- 2. Qdrant Vector Collection (heritage_chunks) ---")
        qdrant_points = 0
        qdrant_point_ids = set()
        try:
            qdrant = get_qdrant_client()
            col_info = qdrant.get_collection(settings.QDRANT_COLLECTION)
            qdrant_points = col_info.points_count or 0
            print(f"  • Collection Name       : {settings.QDRANT_COLLECTION}")
            print(f"  • Qdrant Points Count   : {qdrant_points}")
            print(f"  • Vectors Config        : {col_info.config.params.vectors}")

            # Fetch all point IDs from Qdrant for consistency comparison
            points, _ = qdrant.scroll(
                collection_name=settings.QDRANT_COLLECTION,
                limit=1000,
                with_payload=False,
                with_vectors=False,
            )
            qdrant_point_ids = {str(p.id) for p in points}
        except Exception as e:
            print(f"  • [Notice: Qdrant vector index not started yet or not populated: {e}]")

        # 3. Consistency Validations
        print("\n--- 3. Relational & Vector Consistency Checks ---")
        errors = []

        chunk_reg_cnt = pg_counts.get("chunk_registry", 0)
        if isinstance(chunk_reg_cnt, int) and chunk_reg_cnt > 0:
            if chunk_reg_cnt == qdrant_points:
                print(f"  [PASS] chunk_registry rows ({chunk_reg_cnt}) == Qdrant points ({qdrant_points})")
            else:
                err = f"Mismatch: chunk_registry rows ({chunk_reg_cnt}) != Qdrant points ({qdrant_points})"
                print(f"  [FAIL] {err}")
                errors.append(err)

            db_chunk_ids = {
                str(r[0])
                for r in db.execute(sa_text("SELECT chunk_id FROM chunk_registry")).fetchall()
            }
            if db_chunk_ids == qdrant_point_ids:
                print(f"  [PASS] All {len(db_chunk_ids)} chunk IDs are identical across Postgres and Qdrant.")
            else:
                missing_in_qdrant = len(db_chunk_ids - qdrant_point_ids)
                missing_in_pg = len(qdrant_point_ids - db_chunk_ids)
                err = f"ID set divergence: missing in Qdrant: {missing_in_qdrant}, missing in PG: {missing_in_pg}"
                print(f"  [FAIL] {err}")
                errors.append(err)
        else:
            print("  • chunk_registry has 0 rows (Vector ingest scheduled for Phase 3).")

        orphan_sites = db.execute(sa_text("""
            SELECT count(*) FROM sites WHERE district_id NOT IN (SELECT location_id FROM locations)
        """)).scalar()
        if orphan_sites == 0:
            print("  [PASS] All sites have valid district location references.")
        else:
            err = f"Found {orphan_sites} orphan sites without valid district."
            print(f"  [FAIL] {err}")
            errors.append(err)

    print("\n-------------------------------------------------------")
    if not errors:
        print("RESULT: ALL INTEGRITY AND CONSISTENCY CHECKS PASSED.")
        print("-------------------------------------------------------\n")
        return 0
    else:
        print(f"RESULT: {len(errors)} CONSISTENCY ISSUES DETECTED.")
        print("-------------------------------------------------------\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
