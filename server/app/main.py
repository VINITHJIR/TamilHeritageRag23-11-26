"""FastAPI main application entrypoint for TamilHeritage-GPT backend."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import admin, chat, health, search, sites, users
from app.core.config import settings
from app.services.embeddings import get_embedding_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tamilheritage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Preload bge-m3 embedding model in FastAPI lifespan startup so the first query is not ~11 s
    logger.info("Lifespan startup: Preloading bge-m3 embedding model...")
    try:
        get_embedding_service().load_model()
        logger.info("Lifespan startup: bge-m3 model preloaded successfully.")
    except Exception as e:
        logger.warning(f"Lifespan startup: Failed to preload embedding model: {e}")
    yield
    logger.info("Lifespan shutdown.")


app = FastAPI(
    title="TamilHeritage-GPT API",
    description="Multilingual RAG backend for Tamil Nadu Heritage Sites (Temples, Monuments, Forts, Museums)",
    version="1.0.0",
    lifespan=lifespan,
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
app.include_router(users.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/")
def root():
    return {
        "project": "TamilHeritage-GPT",
        "description": "Multilingual (Tamil / English / Kannada / Tanglish) Heritage RAG API",
        "docs_url": "/docs",
        "health_url": "/api/health",
        "stats_url": "/api/stats"
    }
