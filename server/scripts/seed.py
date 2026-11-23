"""Idempotent database and vector store seeder.

Steps:
  (1) seed.sql      -> sites, locations, categories, aliases, sources, site_facts
  (2) chunks.json   -> embed and ingest chunks into Qdrant and chunk_registry
  (3) seed_chat.sql -> question_types, users, chat_sessions, messages, citations, feedback, logs
"""

import argparse
import logging
import sys
from pathlib import Path

# Add server directory to sys.path
server_root = Path(__file__).resolve().parent.parent
if str(server_root) not in sys.path:
    sys.path.insert(0, str(server_root))

import psycopg
from sqlalchemy import text as sa_text
from app.core.config import settings
from app.db.postgres import SessionLocal
from app.services.ingest import ingest_from_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed")


def get_conn_url() -> str:
    return settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


def run_step_1(force: bool = False):
    """Step 1: Seed sites, locations, categories, aliases, sources, facts."""
    logger.info("--- Step 1: Seeding sites & taxonomy from seed.sql ---")
    seed_sql_path = server_root / "db" / "seed" / "seed.sql"
    if not seed_sql_path.exists():
        seed_sql_path = server_root.parent / "_handoff" / "seed.sql"

    with SessionLocal() as db:
        site_count = db.execute(sa_text("SELECT count(*) FROM sites")).scalar() or 0
        if site_count > 0 and not force:
            logger.info(f"Sites table already has {site_count} rows. Skipping Step 1 (use --force to re-seed).")
            return

    with open(seed_sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    logger.info(f"Executing {seed_sql_path.name}...")
    with psycopg.connect(get_conn_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_content)
        conn.commit()

    with SessionLocal() as db:
        sites_cnt = db.execute(sa_text("SELECT count(*) FROM sites")).scalar()
        aliases_cnt = db.execute(sa_text("SELECT count(*) FROM site_aliases")).scalar()
        categories_cnt = db.execute(sa_text("SELECT count(*) FROM categories")).scalar()
        locations_cnt = db.execute(sa_text("SELECT count(*) FROM locations")).scalar()

        logger.info(
            f"Step 1 Complete: {sites_cnt} sites, {aliases_cnt} aliases, "
            f"{categories_cnt} categories, {locations_cnt} locations."
        )


def run_step_2(force: bool = False):
    """Step 2: Ingest text chunks into Qdrant and chunk_registry."""
    logger.info("--- Step 2: Embedding & Ingesting chunks from chunks.json ---")
    chunks_json_path = server_root / "db" / "seed" / "chunks.json"
    if not chunks_json_path.exists():
        chunks_json_path = server_root.parent / "_handoff" / "chunks.json"

    with SessionLocal() as db:
        chunk_reg_cnt = db.execute(sa_text("SELECT count(*) FROM chunk_registry")).scalar() or 0
        if chunk_reg_cnt >= 30 and not force:
            logger.info(f"chunk_registry already contains {chunk_reg_cnt} rows. Skipping Step 2 (use --force to re-ingest).")
            return

    result = ingest_from_file(chunks_json_path)
    logger.info(f"Step 2 Complete: Ingested {result['ingested']} of {result['total']} chunks into Qdrant & chunk_registry.")


def run_step_3(force: bool = False):
    """Step 3: Seed chat history, citations, and evaluation logs from seed_chat.sql."""
    logger.info("--- Step 3: Seeding chat sessions, messages, and citations from seed_chat.sql ---")
    chat_sql_path = server_root / "db" / "seed" / "seed_chat.sql"
    if not chat_sql_path.exists():
        chat_sql_path = server_root.parent / "_handoff" / "seed_chat.sql"

    with SessionLocal() as db:
        msg_cnt = db.execute(sa_text("SELECT count(*) FROM chat_messages")).scalar() or 0
        if msg_cnt > 0 and not force:
            logger.info(f"chat_messages table already has {msg_cnt} rows. Skipping Step 3 (use --force to re-seed).")
            return

    with open(chat_sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    logger.info(f"Executing {chat_sql_path.name}...")
    with psycopg.connect(get_conn_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_content)
        conn.commit()

    with SessionLocal() as db:
        messages_cnt = db.execute(sa_text("SELECT count(*) FROM chat_messages")).scalar()
        citations_cnt = db.execute(sa_text("SELECT count(*) FROM message_citations")).scalar()
        users_cnt = db.execute(sa_text("SELECT count(*) FROM users")).scalar()

        logger.info(
            f"Step 3 Complete: {users_cnt} users, {messages_cnt} chat_messages, "
            f"{citations_cnt} message_citations."
        )


def main():
    parser = argparse.ArgumentParser(description="Seed database and vector index.")
    parser.add_argument("--step", type=int, choices=[1, 2, 3], help="Run only a specific seed step (1, 2, or 3)")
    parser.add_argument("--force", action="store_true", help="Force re-execution of steps even if tables have data")
    args = parser.parse_args()

    if args.step == 1:
        run_step_1(force=args.force)
    elif args.step == 2:
        run_step_2(force=args.force)
    elif args.step == 3:
        run_step_3(force=args.force)
    else:
        # Run all 3 steps in sequence
        run_step_1(force=args.force)
        run_step_2(force=args.force)
        run_step_3(force=args.force)


if __name__ == "__main__":
    main()
