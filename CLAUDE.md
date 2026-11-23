# TamilHeritage-GPT — Project Context

## Overview
TamilHeritage-GPT is a multilingual (Tamil / English / Kannada / Tanglish) Retrieval-Augmented Generation (RAG) chatbot for Tamil Nadu heritage sites (temples, monuments, museums, forts, palaces, heritage tourist spots). This project is prepared for publication in an academic journal. Code must remain clean, modular, reproducible, and well-documented.

## Core Architecture
- **PostgreSQL 17** (`Heritage_DBJournal`): System of record / single source of truth for sites, locations, categories, aliases, sources, facts, chunk metadata, users, chat messages, citations, feedback, and query logs.
- **Qdrant Vector Database** (`tamilheritage-qdrant` on Docker): Derived vector search index. Can always be rebuilt from PostgreSQL + raw chunks.
- **Entity Linking**: Linked by `site_id` (e.g., `TN-TNJ-0001`) and `chunk_id` (identical deterministic UUID5 across PostgreSQL `chunk_registry.chunk_id` and Qdrant point ID).
- **Embedding Model**: `BAAI/bge-m3` via `sentence-transformers`, 1024-dimension dense vector, normalized, COSINE distance. Lazy loaded as a singleton. Runs on `cuda` if available, otherwise `cpu`. (First download ~2.3 GB).
- **Qdrant Collection**: `heritage_chunks` with named vectors `"dense"` (1024-dim, COSINE) and an empty sparse vector config `"sparse"` (reserved for Level 3 hybrid search).
- **Point ID Formula**: `uuid5(UUID("6f1c2a52-6b7e-4d0e-9a51-3c0f2b7e9d11"), f"{site_id}|{section}|{lang}|{sha256(text)}")`
- **Embedded Text Format**: `f"{name_en} | {name_ta} | {section}\n{text}"`

## Strict Safety & Database Rules
1. **Scope**: Work ONLY inside project root `"D:\2026\BIT\PAPER\TAMIL HERITAGE RAG"` and ONLY inside database `Heritage_DBJournal`.
2. **Never Touch Other DBs**: Never alter, drop, or query any other database on this PostgreSQL server.
3. **No DROP DATABASE**: Never run `DROP DATABASE`. Any reset script must require an explicit `--yes` flag.
4. **Existing Tables**: If tables already exist in `Heritage_DBJournal`, STOP and confirm with user before running any migrations or destructive commands.
5. **Credentials**: Never commit `.env`. Never print `DATABASE_URL` or passwords in logs, console outputs, or responses. All secrets belong strictly in `server/.env`.

## Project Structure
```
TAMIL HERITAGE RAG/
├─ CLAUDE.md                 # Context & instructions for agent sessions
├─ README.md                 # Setup & run instructions for Windows (PowerShell)
├─ .gitignore                # Git exclusions
├─ docker-compose.yml        # Qdrant vector database container
├─ _handoff/                 # Original reference files (schema, seeds, chunks)
├─ server/                   # Backend API (FastAPI)
│  ├─ .env / .env.example
│  ├─ requirements.txt       # Pinned dependencies
│  ├─ app/
│  │  ├─ main.py             # FastAPI entrypoint, CORS, routers
│  │  ├─ core/config.py      # Pydantic Settings
│  │  ├─ db/postgres.py      # SQLAlchemy 2.x engine + psycopg 3 session
│  │  ├─ db/qdrant.py        # Qdrant client + ensure_collection()
│  │  ├─ services/
│  │  │  ├─ embeddings.py    # BAAI/bge-m3 singleton service
│  │  │  ├─ ingest.py        # Ingestion logic into Qdrant & chunk_registry
│  │  │  ├─ gemini.py        # Google Gemini LLM API client, query classification, grounded answer synthesis
│  │  │  ├─ domain_tiers.py  # Domain tiers (A/B/C) and Bayesian Beta reliability updates
│  │  │  ├─ content_dedup.py # Exact SHA-256 and MinHash character 3-shingle clustering
│  │  │  ├─ fact_consensus.py# Deterministic consensus rules, normalizers, conflict detection
│  │  │  ├─ writeback.py     # Calibrated writeback gating & dual atomic persistence
│  │  │  ├─ augment_orchestrator.py # IC³-WB self-augmentation pipeline orchestrator
│  │  │  └─ chat_pipeline.py # Conversational pipeline with identity verification & augmentation fallback
│  │  ├─ api/routes/         # health, sites, search, admin, chat, users
│  │  └─ schemas/            # Pydantic models
│  ├─ db/
│  │  ├─ migrations/         # 001_init.sql, 002_level1b.sql, 003_self_augment.sql
│  │  └─ seed/               # seed.sql, chunks.json, seed_chat.sql
│  ├─ scripts/
│  │  ├─ migrate.py          # Applies migrations with schema_migrations table
│  │  ├─ seed.py             # Idempotent 3-step seeder
│  │  ├─ check.py            # Consistency & row count checker
│  │  └─ reset_dev.py        # Truncates tables + resets Qdrant (--yes required)
│  └─ tests/                 # pytest test suite
└─ client/                   # Frontend (React + Vite, JavaScript)
   ├─ vite.config.js         # Proxy /api -> http://localhost:8000
   └─ src/                   # Components, pages, api client
```

## Common Developer Commands (PowerShell)

### Virtual Environment & Dependencies
```powershell
# Activate venv
.\server\.venv\Scripts\Activate.ps1

# Install requirements
uv pip install --python .\server\.venv\Scripts\python.exe -r .\server\requirements.txt
```

### Database & Vector Ops
```powershell
# Start Qdrant container
docker compose up -d

# Apply PostgreSQL migrations
python .\server\scripts\migrate.py

# Seed data (PostgreSQL seed -> Qdrant & chunk_registry -> chat seed)
python .\server\scripts\seed.py

# Check row counts & vector consistency
python .\server\scripts\check.py

# Reset dev environment (requires explicit --yes)
python .\server\scripts\reset_dev.py --yes
```

### Running Backend & Frontend
```powershell
# Run FastAPI server (port 8000)
.\server\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir server --host 0.0.0.0 --port 8000 --reload

# Run Frontend (port 5173)
cd client
npm install
npm run dev
```

### Running Tests
```powershell
.\server\.venv\Scripts\python.exe -m pytest .\server\tests -v
```
