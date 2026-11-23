"""FastAPI main application entrypoint for TamilHeritage-GPT backend."""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import admin, health, search, sites
from app.core.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tamilheritage")

app = FastAPI(
    title="TamilHeritage-GPT API",
    description="Multilingual RAG backend for Tamil Nadu Heritage Sites (Temples, Monuments, Forts, Museums)",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers under /api
app.include_router(health.router, prefix="/api")
app.include_router(sites.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.get("/")
def root():
    return {
        "project": "TamilHeritage-GPT",
        "description": "Multilingual (Tamil / English / Kannada / Tanglish) Heritage RAG API",
        "docs_url": "/docs",
        "health_url": "/api/health",
        "stats_url": "/api/stats"
    }
