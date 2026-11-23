"""Phase 0 connectivity check for Heritage_DBJournal.

Reads server/.env without logging sensitive credentials, checks connection,
verifies existing table count, and checks pg_trgm extension availability.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def main():
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        print(f"ERROR: {env_file} does not exist.")
        sys.exit(1)

    with open(env_file, "r", encoding="utf-8") as f:
        env_content = f.read()

    db_match = re.search(r"^\s*DATABASE_URL\s*=\s*(.+?)\s*$", env_content, re.MULTILINE)
    if not db_match:
        print("ERROR: DATABASE_URL not found in server/.env")
        sys.exit(1)

    db_url = db_match.group(1).strip().strip('"').strip("'")
    if "<PASSWORD>" in db_url:
        print("STATUS: server/.env contains placeholder <PASSWORD>.")
        print("Please replace <PASSWORD> with your PostgreSQL password in server/.env.")
        sys.exit(2)

    # Normalize driver prefix for parsing (postgresql+psycopg -> postgresql)
    clean_url = db_url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlparse(clean_url)

    db_name = parsed.path.lstrip("/")
    if db_name != "Heritage_DBJournal":
        print(f"SAFETY ERROR: Target database must be 'Heritage_DBJournal', got '{db_name}'. Aborting.")
        sys.exit(1)

    host = parsed.hostname or "localhost"
    port = str(parsed.port or 5432)
    user = parsed.username or "postgres"
    password = parsed.password or ""

    # Locate psql or try psycopg
    psql_path = r"C:\Program Files\PostgreSQL\17\bin\psql.exe"
    if not os.path.exists(psql_path):
        # Search on PATH
        from shutil import which
        psql_path = which("psql")

    if not psql_path:
        print("ERROR: psql executable not found.")
        sys.exit(1)

    sql_check = (
        "SELECT current_database(); "
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'; "
        "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'pg_trgm';"
    )

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    cmd = [
        str(psql_path),
        "-h", host,
        "-p", port,
        "-U", user,
        "-d", db_name,
        "-t", "-A",
        "-c", sql_check
    ]

    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
        lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]

        current_db = lines[0] if len(lines) > 0 else "unknown"
        table_count = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else -1
        pg_trgm_info = lines[2] if len(lines) > 2 else "unavailable"

        print("=== Phase 0 Database Check ===")
        print(f"Connected database: {current_db}")
        print(f"Existing public tables: {table_count}")
        print(f"pg_trgm extension status: {pg_trgm_info}")

        if table_count > 0:
            print("WARNING: Tables already exist in Heritage_DBJournal! Per safety rules, manual confirmation required.")
        else:
            print("Heritage_DBJournal is clean (0 existing tables). Ready for migrations.")

        if "pg_trgm" in pg_trgm_info:
            print("pg_trgm extension is available.")
        else:
            print("WARNING: pg_trgm extension not found in pg_available_extensions.")

        return 0

    except subprocess.CalledProcessError as e:
        # Never print command or credentials
        err_msg = e.stderr.strip() if e.stderr else str(e)
        print("Database connection failed. Error:")
        print(err_msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
