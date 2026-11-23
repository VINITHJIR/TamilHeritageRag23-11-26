"""Regression tests for /api/health (Level 1B Phase 0).

The dashboard showed "Failed to fetch health" because the route raised (HTTP 500).
These tests pin the contract: always 200, never loads bge-m3, degrades per component.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.services import embeddings
from app.api.routes import health as health_route


def _client():
    return TestClient(app, raise_server_exceptions=False)


def test_health_ok_and_does_not_load_model(monkeypatch):
    calls = []
    monkeypatch.setattr(embeddings.EmbeddingService, "load_model", lambda self: calls.append(1))
    r = _client().get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"postgres", "qdrant", "embed_model_loaded"}
    assert isinstance(body["embed_model_loaded"], bool)
    assert calls == [], "health check must never load the embedding model"


def test_health_degrades_when_qdrant_down(monkeypatch):
    class Down:
        def get_collections(self):
            raise ConnectionError("qdrant unreachable")

    monkeypatch.setattr(health_route, "get_qdrant_probe_client", lambda: Down())
    r = _client().get("/api/health")
    assert r.status_code == 200
    assert r.json()["qdrant"] == "error"


def test_health_degrades_when_model_check_raises(monkeypatch):
    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(health_route, "is_embedding_model_loaded", boom)
    r = _client().get("/api/health")
    assert r.status_code == 200
    assert r.json()["embed_model_loaded"] is False
