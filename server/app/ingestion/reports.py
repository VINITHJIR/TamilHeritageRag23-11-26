"""Ingestion audit and provenance reporting module.

Method
------
Generates reproducible execution reports for journal publication and data verification.
Outputs stored in:
    server/data/reports/<run_id>/
Files produced:
1. summary.md:
   - High-level execution status, timestamp, duration, dry-run flag.
   - Per-step pipeline funnel counts (rows_in, inserted, updated, skipped, review).
   - Sites breakdown by primary category.
   - Sites breakdown by district.
2. unmatched.csv:
   - Entities or raw records that could not be mapped to existing sites or known geography.
3. review_queue.csv:
   - All records dispatched to the review queue during this run with reason and payload.
4. category_rules_hit.csv:
   - Frequency distribution of classification rules (R01..R17) that fired, documenting
     rule coverage for the paper.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reports"


def generate_run_report(
    run_id: int,
    step_runs: List[Dict[str, Any]],
    review_items: Optional[List[Dict[str, Any]]] = None,
    unmatched_records: Optional[List[Dict[str, Any]]] = None,
    rules_hit_counts: Optional[Dict[str, int]] = None,
    category_counts: Optional[Dict[str, int]] = None,
    district_counts: Optional[Dict[str, int]] = None,
    dry_run: bool = False,
    output_base_dir: Optional[Path] = None
) -> Path:
    """Generate the complete suite of markdown and CSV audit reports for a pipeline run.

    Args:
        run_id: Unique pipeline execution run identifier.
        step_runs: List of step execution summary dictionaries.
        review_items: Items written or routed to review_queue.
        unmatched_records: Records that could not be matched.
        rules_hit_counts: Dict mapping rule_id -> count of hits.
        category_counts: Dict mapping primary_category -> count of sites.
        district_counts: Dict mapping district -> count of sites.
        dry_run: Whether execution was a dry-run.
        output_base_dir: Optional custom reports directory.

    Returns:
        Path to the generated report directory.
    """
    base = Path(output_base_dir or DEFAULT_REPORTS_DIR).resolve()
    run_dir = base / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    review_items = review_items or []
    unmatched_records = unmatched_records or []
    rules_hit_counts = rules_hit_counts or {}
    category_counts = category_counts or {}
    district_counts = district_counts or {}

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. summary.md
    summary_lines = [
        f"# TamilHeritage-GPT Ingestion Report: Run {run_id}",
        "",
        f"- **Generated At**: {now_iso}",
        f"- **Mode**: {'DRY RUN (No database/vector store mutations)' if dry_run else 'LIVE RUN'}",
        f"- **Total Review Queue Items**: {len(review_items)}",
        f"- **Total Unmatched Records**: {len(unmatched_records)}",
        "",
        "## 1. Pipeline Steps Funnel",
        "",
        "| Step | Status | Rows In | Inserted | Updated | Skipped | Sent to Review | Error |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ]

    for s in step_runs:
        summary_lines.append(
            f"| `{s.get('step', '')}` | **{s.get('status', 'success')}** | "
            f"{s.get('rows_in', 0)} | {s.get('inserted', 0)} | {s.get('updated', 0)} | "
            f"{s.get('skipped', 0)} | {s.get('sent_to_review', 0)} | {s.get('error') or '-'} |"
        )

    summary_lines.extend([
        "",
        "## 2. Sites by Primary Category",
        "",
        "| Category | Count |",
        "| :--- | :--- |"
    ])
    for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        summary_lines.append(f"| `{cat}` | **{count}** |")

    summary_lines.extend([
        "",
        "## 3. Sites by District",
        "",
        "| District | Count |",
        "| :--- | :--- |"
    ])
    for dist, count in sorted(district_counts.items(), key=lambda x: x[1], reverse=True):
        summary_lines.append(f"| {dist} | **{count}** |")

    (run_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    # 2. unmatched.csv
    unmatched_path = run_dir / "unmatched.csv"
    with open(unmatched_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "source_key", "name", "district", "locality", "reason"])
        for u in unmatched_records:
            writer.writerow([
                u.get("source", ""),
                u.get("source_key", ""),
                u.get("name", ""),
                u.get("district", ""),
                u.get("locality", ""),
                u.get("reason", "")
            ])

    # 3. review_queue.csv
    review_path = run_dir / "review_queue.csv"
    with open(review_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["kind", "source", "source_key", "reason", "status", "payload"])
        for rq in review_items:
            writer.writerow([
                rq.get("kind", ""),
                rq.get("source", ""),
                rq.get("source_key", ""),
                rq.get("reason", ""),
                rq.get("status", "open"),
                json.dumps(rq.get("payload", {}), ensure_ascii=False)
            ])

    # 4. category_rules_hit.csv
    rules_path = run_dir / "category_rules_hit.csv"
    with open(rules_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rule_id", "rule_description", "hits"])
        for rid, count in sorted(rules_hit_counts.items()):
            writer.writerow([rid, f"Rule {rid}", count])

    logger.info(f"Audit report successfully generated at: {run_dir}")
    return run_dir
