"""Development environment reset script.

Safety:
  - Requires explicit --yes argument to execute.
  - Truncates only project tables in Heritage_DBJournal (never runs DROP DATABASE).
  - Deletes and re-creates Qdrant collection 'heritage_chunks'.
"""

import argparse
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# Ensure server root in sys.path
server_root = Path(__file__).resolve().parent.parent
if str(server_root) not in sys.path:
    sys.path.insert(0, str(server_root))

from sqlalchemy import text as sa_text
from sqlalchemy import create_engine
from app.core.config import settings
from app.db.qdrant import get_qdrant_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reset_dev")


def get_safe_engine():
    env_file = server_root / ".env"
    if not env_file.exists():
        logger.error(f"{env_file} does not exist.")
        sys.exit(1)

    with open(env_file, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r"^\s*DATABASE_URL\s*=\s*(.+?)\s*$", content, re.MULTILINE)
    if not match:
        logger.error("DATABASE_URL not found in server/.env")
        sys.exit(1)

    db_url = match.group(1).strip().strip('"').strip("'")
    if "<PASSWORD>" in db_url:
        logger.error("DATABASE_URL contains placeholder <PASSWORD>.")
        sys.exit(1)

    parsed = urlparse(db_url.replace("postgresql+psycopg://", "postgresql://"))
    db_name = parsed.path.lstrip("/")
    if db_name != "Heritage_DBJournal":
        logger.error(f"SAFETY ERROR: Target database must be 'Heritage_DBJournal', got '{db_name}'. Aborting.")
        sys.exit(1)

    return create_engine(db_url, isolation_level="AUTOCOMMIT")


def main():
    parser = argparse.ArgumentParser(
        description="Reset TamilHeritage-GPT development database and vector collection."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Explicit confirmation required to execute destructive reset."
    )
    args = parser.parse_args()

    if not args.yes:
        print("ERROR: Safety confirmation missing.")
        print("Any destructive reset requires the explicit '--yes' flag.")
        print("Usage: python server/scripts/reset_dev.py --yes")
        sys.exit(1)

    print("WARNING: This will truncate all project tables in Heritage_DBJournal")
    print("and delete the 'heritage_chunks' collection in Qdrant.")

    engine = get_safe_engine()

    tables = [
        "query_logs",
        "message_feedback",
        "message_citations",
        "chat_messages",
        "chat_sessions",
        "users",
        "question_types",
        "chunk_registry",
        "site_facts",
        "sources",
        "site_aliases",
        "sites",
        "locations",
        "categories",
        "schema_migrations",
    ]

    with engine.connect() as conn:
        for tbl in tables:
            try:
                conn.execute(sa_text(f"TRUNCATE TABLE {tbl} CASCADE;"))
                logger.info(f"Truncated table {tbl}.")
            except Exception as e:
                logger.warning(f"Could not truncate {tbl} (might not exist yet): {e}")

    try:
        qdrant = get_qdrant_client()
        collections_resp = qdrant.get_collections()
        existing = [c.name for c in collections_resp.collections]
        if settings.QDRANT_COLLECTION in existing:
            qdrant.delete_collection(collection_name=settings.QDRANT_COLLECTION)
            logger.info(f"Deleted Qdrant collection '{settings.QDRANT_COLLECTION}'.")
    except Exception as e:
        logger.warning(f"Could not reset Qdrant collection: {e}")

    logger.info("Development environment reset complete.")


if __name__ == "__main__":
    main()
