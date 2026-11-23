"""Raw data acquisition and ingestion run tracking.

Method:
-------
This module scans the raw data landing zone (server/data/raw/**) and registers files
by computing cryptographic SHA-256 digests, file sizes, and modification timestamps.
It provides idempotency checks against the PostgreSQL `ingestion_runs` table: if a raw
file with an identical SHA-256 hash has already been successfully ingested for a given
pipeline step, subsequent runs can safely skip redundant re-processing.

Audit Trail:
------------
Every pipeline stage records an execution entry in `ingestion_runs` documenting start/finish
timestamps, execution status ('running', 'success', 'failed', 'skipped'), input file provenance,
and metrics (rows_in, inserted, updated, skipped, sent_to_review, error).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
from typing import Any, List, Optional
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Default raw data landing directory relative to project server folder
DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"


@dataclass
class RawFileInfo:
    """Metadata container for a scanned raw input file."""
    rel_path: str
    abs_path: Path
    sha256: str
    size_bytes: int
    mtime: datetime


def compute_file_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    """Compute the SHA-256 cryptographic digest of a file in streaming chunks.

    Args:
        file_path: Path to the target file.
        chunk_size: Byte block size for chunked streaming (default: 64 KB).

    Returns:
        Hex-encoded SHA-256 string.
    """
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def scan_raw_files(base_dir: Optional[Path] = None) -> List[RawFileInfo]:
    """Recursively scan server/data/raw for files and collect provenance metadata.

    Args:
        base_dir: Optional root directory to scan. Defaults to server/data/raw.

    Returns:
        List of RawFileInfo records sorted by relative path.
    """
    root = Path(base_dir or DEFAULT_RAW_DIR).resolve()
    if not root.exists():
        logger.warning(f"Raw data directory does not exist: {root}")
        return []

    collected: List[RawFileInfo] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            try:
                stat = path.stat()
                rel_path = str(path.relative_to(root)).replace("\\", "/")
                file_hash = compute_file_sha256(path)
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                collected.append(RawFileInfo(
                    rel_path=rel_path,
                    abs_path=path,
                    sha256=file_hash,
                    size_bytes=stat.st_size,
                    mtime=mtime
                ))
            except Exception as e:
                logger.error(f"Error inspecting raw file {path}: {e}")

    return collected


def is_already_processed(db: Session, sha256_hash: str, step: str) -> bool:
    """Check if an input file hash has already been successfully processed for a pipeline step.

    Args:
        db: Active SQLAlchemy session.
        sha256_hash: SHA-256 digest of the input file.
        step: Pipeline step name (e.g. 'lgd', 'asi', 'wikidata').

    Returns:
        True if an existing run with status 'success' exists for this hash and step.
    """
    stmt = sa_text("""
        SELECT 1 FROM ingestion_runs
        WHERE input_sha256 = :sha256
          AND step = :step
          AND status = 'success'
        LIMIT 1
    """)
    result = db.execute(stmt, {"sha256": sha256_hash, "step": step}).scalar()
    return result is not None


def start_ingestion_run(
    db: Session,
    step: str,
    source: Optional[str] = None,
    input_file: Optional[str] = None,
    input_sha256: Optional[str] = None
) -> int:
    """Register the commencement of a pipeline stage in ingestion_runs.

    Args:
        db: Active SQLAlchemy session.
        step: Step name ('lgd', 'asi', 'classify', 'group', 'wikidata', 'wikipedia', 'chunks', 'load').
        source: Source identifier ('lgd', 'asi', 'wikidata', etc.).
        input_file: Relative path to input file.
        input_sha256: SHA-256 digest of input file.

    Returns:
        Assigned run_id integer.
    """
    stmt = sa_text("""
        INSERT INTO ingestion_runs (
            step, source, input_file, input_sha256, started_at, status
        ) VALUES (
            :step, :source, :input_file, :input_sha256, now(), 'running'
        )
        RETURNING run_id
    """)
    run_id = db.execute(stmt, {
        "step": step,
        "source": source,
        "input_file": input_file,
        "input_sha256": input_sha256
    }).scalar_one()
    db.commit()
    return run_id


def finish_ingestion_run(
    db: Session,
    run_id: int,
    status: str = "success",
    rows_in: int = 0,
    inserted: int = 0,
    updated: int = 0,
    skipped: int = 0,
    sent_to_review: int = 0,
    error: Optional[str] = None,
    **kwargs: Any
) -> None:
    """Finalize an ingestion run record with outcome metrics and status.

    Args:
        db: Active SQLAlchemy session.
        run_id: ID of the running stage.
        status: 'success' | 'failed' | 'skipped'.
        rows_in: Input row/record count.
        inserted: Newly inserted count.
        updated: Updated existing record count.
        skipped: Unchanged or skipped record count.
        sent_to_review: Records routed to review_queue.
        error: Error message string if failed.
    """
    stmt = sa_text("""
        UPDATE ingestion_runs
        SET finished_at = now(),
            status = :status,
            rows_in = :rows_in,
            inserted = :inserted,
            updated = :updated,
            skipped = :skipped,
            sent_to_review = :sent_to_review,
            error = :error
        WHERE run_id = :run_id
    """)
    db.execute(stmt, {
        "run_id": run_id,
        "status": status,
        "rows_in": rows_in,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "sent_to_review": sent_to_review,
        "error": error
    })
    db.commit()
