"""Applies SQL migration files in order and tracks them in schema_migrations."""

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
import psycopg

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate")


def get_safe_db_url() -> str:
    env_file = Path(__file__).resolve().parent.parent / ".env"
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
        logger.error("DATABASE_URL still contains placeholder <PASSWORD>. Please update server/.env.")
        sys.exit(1)

    # Normalize driver prefix for psycopg
    clean_url = db_url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlparse(clean_url)
    db_name = parsed.path.lstrip("/")
    if db_name != "Heritage_DBJournal":
        logger.error(f"SAFETY ERROR: Target database must be 'Heritage_DBJournal', got '{db_name}'. Aborting.")
        sys.exit(1)

    return clean_url


def main():
    parser = argparse.ArgumentParser(description="Apply PostgreSQL database migrations.")
    args = parser.parse_args()

    clean_url = get_safe_db_url()
    migrations_dir = Path(__file__).resolve().parent.parent / "db" / "migrations"
    if not migrations_dir.exists():
        logger.error(f"Migrations directory not found: {migrations_dir}")
        sys.exit(1)

    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        logger.warning(f"No .sql migration files found in {migrations_dir}")
        return

    with psycopg.connect(clean_url) as conn:
        with conn.cursor() as cur:
            # Create migrations tracking table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id SERIAL PRIMARY KEY,
                    filename     TEXT UNIQUE NOT NULL,
                    applied_at   TIMESTAMPTZ DEFAULT now()
                );
            """)
            conn.commit()

            # Fetch already applied migrations
            cur.execute("SELECT filename FROM schema_migrations;")
            rows = cur.fetchall()
            applied = {r[0] for r in rows}

            for mig in migration_files:
                filename = mig.name
                if filename in applied:
                    logger.info(f"Skipping already applied migration: {filename}")
                    continue

                logger.info(f"Applying migration: {filename}...")
                with open(mig, "r", encoding="utf-8") as f:
                    sql_content = f.read()

                # Execute migration and record tracking in a single transaction
                cur.execute(sql_content)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s);",
                    (filename,)
                )
                conn.commit()
                logger.info(f"Successfully applied {filename}.")

    logger.info("All migrations are up to date.")


if __name__ == "__main__":
    main()
