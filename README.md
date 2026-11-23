# TamilHeritage-GPT (Level 1)

Multilingual (Tamil / English / Kannada / Tanglish) RAG Chatbot for Tamil Nadu Heritage Sites.

## Prerequisites
- **OS**: Windows 10/11 (PowerShell)
- **Python**: 3.11+ (Python 3.12 recommended)
- **Node.js**: v18+ (tested on v24)
- **PostgreSQL**: 17 running locally on port 5432 with empty database `Heritage_DBJournal`
- **Docker Desktop**: Running for Qdrant vector database

---

## Setup Instructions

### 1. Environment Configuration
Create `server/.env` based on `server/.env.example`:
```powershell
Copy-Item server\.env.example server\.env
```
Fill in your PostgreSQL password in `server/.env`:
```env
DATABASE_URL=postgresql+psycopg://postgres:<PASSWORD>@localhost:5432/Heritage_DBJournal
```

### 2. Python Virtual Environment
```powershell
# Create virtual environment with Python 3.12
uv venv server\.venv --python 3.12

# Install dependencies
uv pip install --python server\.venv\Scripts\python.exe -r server\requirements.txt
```

### 3. Start Qdrant Vector Database
Make sure Docker Desktop is open and running, then start the container:
```powershell
docker compose up -d
```
Qdrant web UI will be available at [http://localhost:6333/dashboard](http://localhost:6333/dashboard).

### 4. Database Migration & Seeding
```powershell
# Apply PostgreSQL schema migration
.\server\.venv\Scripts\python.exe server\scripts\migrate.py

# Seed database, generate embeddings, and populate Qdrant
.\server\.venv\Scripts\python.exe server\scripts\seed.py

# Verify system integrity & row counts
.\server\.venv\Scripts\python.exe server\scripts\check.py
```

### 5. Start Backend Server
```powershell
.\server\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir server --host 0.0.0.0 --port 8000 --reload
```
Interactive API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)

### 6. Install & Start Frontend (Client)
In a new PowerShell terminal:
```powershell
cd client
npm install
npm run dev
```
Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## Running Verification Tests
```powershell
.\server\.venv\Scripts\python.exe -m pytest server\tests\test_level1.py -v
```

## Level 1 Results

Level 1 benchmark verification passed **7/7 tests** across multilingual semantic retrieval, fuzzy trigram ranking, vector database consistency, and ingestion idempotency.

### 1. Relational & Vector Store Counts
- **Sites registered**: 10 (across 7 primary categories: temple, fort, monument, palace, museum, archaeological_site, memorial)
- **Locations**: 22 (districts, taluks, municipal corporations)
- **Categories**: 11
- **Site Aliases**: 25 (covering English, Tamil script, and Tanglish `ta-Latn`)
- **Sources Curated**: 8
- **Site Facts**: 5
- **PostgreSQL `chunk_registry`**: 30 chunks
- **Qdrant `heritage_chunks` Points**: 30 points (1024-dim dense BAAI/bge-m3 embeddings, Cosine distance)
- **ID Consistency**: 100% exact UUID5 match between PostgreSQL `chunk_registry.chunk_id` and Qdrant vector point IDs
- **Chat Benchmark Records**: 5 users, 5 sessions, 16 messages, 11 citations, 4 feedbacks, 8 query logs

### 2. Multilingual Semantic Search Benchmark
| Query Language | Natural Language Benchmark Query | Target Site ID | Top-1 Predicted Site | Score | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tamil (தமிழ்)** | "தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?" | `TN-TNJ-0001` | Brihadeeswarar Temple (`TN-TNJ-0001`) | **0.7106** | **PASS** |
| **English** | "Who built the Big Temple in Thanjavur?" | `TN-TNJ-0001` | Brihadeeswarar Temple (`TN-TNJ-0001`) | **0.6993** | **PASS** |
| **Kannada (ಕನ್ನಡ)** | "ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?" | `TN-ERD-0001` | Bannari Amman Temple (`TN-ERD-0001`) | **0.6927** | **PASS** |
| **Filtered Search** | "where can I see Chola bronze statues" *(category=museum)* | `TN-CHN-0001` | Government Museum, Chennai (`TN-CHN-0001`) | **0.5905** | **PASS** |
| **Fuzzy / Trigram** | `?q=mahabalipuram shore templ` | `TN-CGL-0001` | Monuments at Mamallapuram (`TN-CGL-0001`) | Rank 1 | **PASS** |

### 3. Pipeline Invariance & Idempotency
- Re-running `seed.py` produces **zero duplicate records** across both relational tables and the Qdrant vector index.
- Vector point count remains invariant at 30 points.
