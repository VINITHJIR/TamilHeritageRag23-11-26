"""Application configuration loaded from server/.env using pydantic-settings."""

import json
from pathlib import Path
from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    """Core settings for TamilHeritage-GPT backend."""

    # PostgreSQL Database URL
    DATABASE_URL: str = "postgresql+psycopg://postgres:<PASSWORD>@localhost:5432/Heritage_DBJournal"

    # Qdrant Vector Database
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_COLLECTION: str = "heritage_chunks"

    # Embedding Model (BAAI/bge-m3 dense 1024-dim)
    EMBED_MODEL: str = "BAAI/bge-m3"
    EMBED_DEVICE: str = "auto"  # 'auto' selects cuda if available else cpu

    # Google Gemini LLM Settings
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-flash-lite-latest"
    GEMINI_TIMEOUT_SEC: float = 30.0

    # CORS Origins
    CORS_ORIGINS: List[str] = ["http://localhost:5173"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            val = value.strip()
            if val.startswith("[") and val.endswith("]"):
                try:
                    return json.loads(val)
                except Exception:
                    pass
            return [origin.strip() for origin in val.split(",") if origin.strip()]
        return value

    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
