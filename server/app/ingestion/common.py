"""Shared helpers: JSONL interim files and a polite, caching HTTP client.

Method (HTTP):
    * Descriptive User-Agent (Wikimedia policy), configurable contact via INGEST_CONTACT.
    * Per-host rate limit: at least HTTP_MIN_INTERVAL_S between requests.
    * Retries with exponential back-off on 429/5xx, honouring Retry-After.
    * Every successful response body is cached on disk under server/data/cache/<namespace>/
      keyed by sha256(method + url + params), so re-runs are offline and reproducible.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import urlparse

import httpx

from app.ingestion import config as C

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- JSONL I/O
def write_jsonl(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            n += 1
    tmp.replace(path)
    return n


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def interim(name: str) -> Path:
    return C.INTERIM_DIR / f"{name}.jsonl"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- HTTP client
class CachedHttp:
    """Rate-limited HTTP GET with on-disk JSON/text cache."""

    def __init__(self, namespace: str, offline: bool = False):
        self.cache_dir = C.CACHE_DIR / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self._last_call: dict[str, float] = {}
        self._client: Optional[httpx.Client] = None
        self.hits = 0
        self.misses = 0

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": C.USER_AGENT, "Accept-Encoding": "gzip"},
                timeout=C.HTTP_TIMEOUT_S, follow_redirects=True,
            )
        return self._client

    def _key(self, url: str, params: Optional[dict]) -> Path:
        raw = url + "?" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        return self.cache_dir / (hashlib.sha256(raw.encode("utf-8")).hexdigest() + ".json")

    def get_json(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
        path = self._key(url, params)
        if path.exists():
            self.hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["body"]
        if self.offline:
            raise RuntimeError(f"offline mode and no cache for {url}")
        self.misses += 1
        body = self._fetch(url, params, headers)
        path.write_text(json.dumps({"url": url, "params": params, "fetched_at": time.time(), "body": body},
                                   ensure_ascii=False), encoding="utf-8")
        return body

    def _fetch(self, url: str, params: Optional[dict], headers: Optional[dict]) -> Any:
        host = urlparse(url).netloc
        delay = 2.0
        for attempt in range(1, C.HTTP_MAX_RETRIES + 1):
            wait = C.HTTP_MIN_INTERVAL_S - (time.monotonic() - self._last_call.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_call[host] = time.monotonic()
            try:
                r = self._http().get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning("HTTP error %s (attempt %d): %s", host, attempt, type(exc).__name__)
                time.sleep(delay); delay *= 2
                continue
            if r.status_code == 200 and r.content.strip():
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504) or not r.content.strip():
                retry_after = r.headers.get("Retry-After")
                sleep_s = float(retry_after) if retry_after and retry_after.isdigit() else delay
                logger.warning("HTTP %s from %s (attempt %d); sleeping %.0fs", r.status_code, host, attempt, sleep_s)
                time.sleep(sleep_s); delay *= 2
                continue
            r.raise_for_status()
        raise RuntimeError(f"giving up on {url} after {C.HTTP_MAX_RETRIES} attempts")

    def close(self):
        if self._client is not None:
            self._client.close()


def batched(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
