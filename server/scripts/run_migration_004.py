"""Run migration 004_clarifications.sql against Heritage_DBJournal."""
import os
import psycopg
from pathlib import Path

env_path = Path(__file__).parent.parent / ".env"
db_url = ""
for line in env_path.read_text().splitlines():
    if line.startswith("DATABASE_URL="):
        db_url = line.split("=", 1)[1].strip().replace("postgresql+psycopg://", "postgresql://")
        break

if not db_url:
    raise RuntimeError("DATABASE_URL not found in .env")

sql_file = Path(__file__).parent.parent / "migrations" / "004_clarifications.sql"
sql = sql_file.read_text(encoding="utf-8")

with psycopg.connect(db_url) as conn:
    conn.autocommit = True
    conn.execute(sql)
print("Migration 004_clarifications.sql applied successfully.")
