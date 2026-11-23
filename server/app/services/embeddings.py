"""Embedding service using BAAI/bge-m3 via sentence-transformers.

Provides 1024-dimensional normalized dense vectors for heritage text chunks and queries.
Loaded lazily as a singleton.
"""

import logging
import os
from typing import List, Optional
import torch
from sentence_transformers import SentenceTransformer
from app.core.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Singleton service for BAAI/bge-m3 embeddings."""

    _instance: Optional["EmbeddingService"] = None

    def __init__(self):
        self._model: Optional[SentenceTransformer] = None
        self._device: Optional[str] = None

    @classmethod
    def get_instance(cls) -> "EmbeddingService":
        if cls._instance is None:
            cls._instance = EmbeddingService()
        return cls._instance

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _determine_device(self) -> str:
        if settings.EMBED_DEVICE and settings.EMBED_DEVICE.lower() in ("cuda", "cpu"):
            return settings.EMBED_DEVICE.lower()
        return "cuda" if torch.cuda.is_available() else "cpu"

    def load_model(self) -> None:
        """Lazily load the sentence-transformers model."""
        if self._model is not None:
            return

        self._device = self._determine_device()
        logger.info(
            f"Loading embedding model '{settings.EMBED_MODEL}' on device '{self._device}'... "
            "(Notice: First-time download is approximately 2.3 GB; please ensure adequate bandwidth and disk space)."
        )
        print(
            f"[EmbeddingService] Loading '{settings.EMBED_MODEL}' on '{self._device}'... "
            "(Initial download ~2.3 GB if not cached)"
        )

        self._model = SentenceTransformer(settings.EMBED_MODEL, device=self._device)
        logger.info(f"Model '{settings.EMBED_MODEL}' successfully loaded.")

    def count_tokens(self, text: str) -> int:
        """Calculate token count using the model's tokenizer."""
        self.load_model()
        tokens = self._model.tokenizer.encode(text, add_special_tokens=False)
        return len(tokens)

    def embed_texts(self, texts: List[str], batch_size: int = 16) -> List[List[float]]:
        """Compute 1024-dim normalized dense embeddings for a list of texts."""
        self.load_model()
        embeddings = self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        return embeddings.tolist()

    def embed_query(self, query: str) -> List[float]:
        """Compute normalized dense embedding for a single search query."""
        self.load_model()
        embedding = self._model.encode(
            query,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        return embedding.tolist()

    def embed_text(self, text: str) -> List[float]:
        """Compute normalized dense embedding for a single text chunk."""
        return self.embed_query(text)

    @staticmethod
    def format_chunk_for_embedding(name_en: str, name_ta: Optional[str], section: str, text: str) -> str:
        """Format chunk text according to project specification:

        '{name_en} | {name_ta} | {section}\\n{text}'
        """
        ta_part = name_ta.strip() if name_ta else ""
        return f"{name_en.strip()} | {ta_part} | {section.strip()}\n{text.strip()}"


# Global singleton instance accessor
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService.get_instance()


def is_embedding_model_loaded() -> bool:
    """Report whether bge-m3 is already in memory WITHOUT creating or loading anything.

    Used by /api/health, which must stay fast and side-effect free.
    """
    inst = EmbeddingService._instance
    return inst is not None and inst.is_loaded
