#!/usr/bin/env python3
"""
Observability Report Generator — Phase v3.0 Observability & Monitoring MVP

Read-only CLI/library for generating governance/validation/pool state summary
from Task Pool artifacts (pool.yaml and item JSON files).

Usage:
    python3 scripts/observability_report.py --format json
    python3 scripts/observability_report.py --format markdown
    python3 scripts/observability_report.py --pool-root /tmp/pool --pool-index /tmp/pool/pool.yaml --format json

This script is READ-ONLY: it does not create, modify, move, or delete any pool files.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Default paths aligned with scripts/pool.py
DEFAULT_POOL_ROOT = Path(__file__).parent.parent / "docs" / "agent_context" / "pool"
DEFAULT_POOL_INDEX = DEFAULT_POOL_ROOT / "pool.yaml"
SUBDIRS = ["active", "pending", "blocked", "completed"]

# Required top-level keys in summary output
REQUIRED_TOP_LEVEL_KEYS = [
    "generated_at",
    "pool_root",
    "pool_index",
    "index_item_count",
    "item_file_count",
    "counts_by_status",
    "counts_by_layer",
    "counts_by_lane",
    "counts_by_risk",
    "pilot_counts",
    "retry_summary",
    "validate_summary",
    "governance_signals",
    "integrity_warnings",
]

# Required markdown sections
REQUIRED_MD_SECTIONS = [
    "Overview",
    "Pool State",
    "Validate Gate",
    "Governance Signals",
    "Integrity Warnings",
]


def load_pool_index(pool_index_path: Path) -> dict:
    """
    Read JSON-compatible pool.yaml.

    Returns empty index on missing/empty/invalid file.
    """
    if not pool_index_path.exists():
        return {"items": [], "version": "v1.0", "updated_at": None}

    try:
        content = pool_index_path.read_text(encoding="utf-8").strip()
        if not content:
            return {"items": [], "version": "v1.0", "updated_at": None}
        return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return {"items": [], "version": "v1.0", "updated_at": None}


def discover_item_files(pool_root: Path) -> list[Path]:
    """
    Return item JSON paths under active/pending/blocked/completed.

    Does not mutate any files.
    """
    item_paths = []
    for subdir in SUBDIRS:
        subdir_path = pool_root / subdir
        if subdir_path.exists() and subdir_path.is_dir():
            for item_file in subdir_path.glob("*.json"):
                if item_file.is_file():
                    item_paths.append(item_file)
    return item_paths


def load_item_files(item_paths: list[Path]) -> tuple[list[dict], list[dict]]:
    """
    Load item JSON files.

    Returns (items, warnings). Malformed JSON becomes warning, not exception.
    """
    items = []
    warnings = []

    for item_path in item_paths:
        try:
            content = item_path.read_text(encoding="utf-8")
            item = json.loads(content)
            items.append(item)
        except (json.JSONDecodeError, OSError) as e:
            warnings.append({
                "path": str(item_path),
                "message": f"Failed to parse JSON: {e}",
            })

    return items, warnings


def build_summary(pool_root: Path, pool_index_path: Path) -> dict:
    """
    Build complete observability summary.

    Read-only operation on pool artifacts.
    """
    # Load pool index
    pool_index = load_pool_index(pool_index_path)
    index_items = pool_index.get("items", [])

    # Discover and load item files
    item_paths = discover_item_files(pool_root)
    items, item_warnings = load_item_files(item_paths)

    # Initialize counters
    counts_by_status = defaultdict(int)
    counts_by_layer = defaultdict(int)
    counts_by_lane = defaultdict(int)
    counts_by_risk = defaultdict(int)
    pilot_counts = {"pilot": 0, "task": 0, "is_pilot_true": 0, "is_pilot_false": 0}

    # Retry tracking
    total_retry_count = 0
    max_retry_count = 0
    items_at_or_over_max_retry = 0
    items_with_retries = 0

    # Validate tracking
    validate_total_attempts = 0
    validate_results = defaultdict(int)

    # Governance signals
    l4_mandatory_delegation_count = 0
    pre_approval_count = 0
    blocked_count = 0
    escalated_count = 0
    lane_escalation_count = 0
    hitl_required_count = 0
    qa_required_count = 0

    # Integrity warnings
    integrity_warnings = list(item_warnings)

    # Process each item
    for item in items:
        # Status counting (from item file, fallback to UNKNOWN)
        status = item.get("status", "UNKNOWN")
        counts_by_status[status] += 1

        # blocked_count: count items where item.status == "blocked"
        if status == "blocked":
            blocked_count += 1

        # escalated_count: count items where status == "escalated" OR
        # lane_decision.escalation_triggered == True
        if status == "escalated":
            escalated_count += 1

        lane_decision = item.get("lane_decision", {})
        if lane_decision.get("escalation_triggered", False) is True:
            lane_escalation_count += 1
            # Only increment escalated_count if not already counted via status
            if status != "escalated":
                escalated_count += 1

        # Layer counting: execution_contract.recommended_layer fallback classifier_result.final_layer
        execution_contract = item.get("execution_contract", {})
        recommended_layer = execution_contract.get("recommended_layer")
        if not recommended_layer:
            classifier_result = item.get("classifier_result", {})
            recommended_layer = classifier_result.get("final_layer", "UNKNOWN")
        if not recommended_layer:
            recommended_layer = "UNKNOWN"
        counts_by_layer[recommended_layer] += 1

        # Lane counting: lane_decision.lane, fallback UNKNOWN
        lane = lane_decision.get("lane", "UNKNOWN")
        counts_by_lane[lane] += 1

        # Risk counting: execution_contract.risk_level, fallback UNKNOWN
        risk_level = execution_contract.get("risk_level", "UNKNOWN")
        counts_by_risk[risk_level] += 1

        # Pilot counting
        is_pilot = item.get("is_pilot", False)
        artifact_type = item.get("artifact_type", "task")
        if is_pilot:
            pilot_counts["is_pilot_true"] += 1
            pilot_counts["pilot"] += 1
        else:
            pilot_counts["is_pilot_false"] += 1
            if artifact_type == "pilot":
                pilot_counts["pilot"] += 1
            else:
                pilot_counts["task"] += 1

        # Retry summary
        retry_count = item.get("retry_count", 0)
        max_retry = item.get("max_retry", 3)
        if retry_count is None:
            retry_count = 0
        total_retry_count += retry_count
        if retry_count > max_retry_count:
            max_retry_count = retry_count
        if retry_count >= max_retry:
            items_at_or_over_max_retry += 1
        if retry_count > 0:
            items_with_retries += 1

        # Validate summary
        validate_history = item.get("validate_history", [])
        if validate_history is None:
            validate_history = []
        validate_total_attempts += len(validate_history)
        for vh in validate_history:
            result = vh.get("result", "UNKNOWN")
            validate_results[result] += 1

        # Governance signals (use safe .get with False defaults for items
        # created via pool.py add without lane_decision fields)
        if lane_decision.get("l4_mandatory_delegation", False) is True:
            l4_mandatory_delegation_count += 1

        hitl_mode = lane_decision.get("hitl_mode", "")
        if hitl_mode == "pre_approval":
            pre_approval_count += 1

        if lane_decision.get("hitl_required", False) is True:
            hitl_required_count += 1

        if lane_decision.get("qa_required", False) is True:
            qa_required_count += 1

    # Build governance_signals dict
    governance_signals = {
        "l4_mandatory_delegation_count": l4_mandatory_delegation_count,
        "pre_approval_count": pre_approval_count,
        "blocked_count": blocked_count,
        "escalated_count": escalated_count,
        "lane_escalation_count": lane_escalation_count,
        "hitl_required_count": hitl_required_count,
        "qa_required_count": qa_required_count,
    }

    # Build validate_summary dict
    validate_summary = {
        "total_attempts": validate_total_attempts,
        "results": dict(validate_results),
    }

    # Build retry_summary dict
    retry_summary = {
        "total_retry_count": total_retry_count,
        "max_retry_count": max_retry_count,
        "items_at_or_over_max_retry": items_at_or_over_max_retry,
        "items_with_retries": items_with_retries,
    }

    # Build summary dict with all required top-level keys
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_root": str(pool_root),
        "pool_index": str(pool_index_path),
        "index_item_count": len(index_items),
        "item_file_count": len(items),
        "counts_by_status": dict(counts_by_status),
        "counts_by_layer": dict(counts_by_layer),
        "counts_by_lane": dict(counts_by_lane),
        "counts_by_risk": dict(counts_by_risk),
        "pilot_counts": pilot_counts,
        "retry_summary": retry_summary,
        "validate_summary": validate_summary,
        "governance_signals": governance_signals,
        "integrity_warnings": integrity_warnings,
    }

    return summary


def render_markdown(summary: dict) -> str:
    """
    Render human-readable Markdown summary.
    """
    lines = []

    # Overview
    lines.append("# Observability Report\n")
    lines.append("## Overview\n")
    lines.append(f"- **Generated at:** {summary.get('generated_at', 'N/A')}")
    lines.append(f"- **Pool root:** `{summary.get('pool_root', 'N/A')}`")
    lines.append(f"- **Pool index:** `{summary.get('pool_index', 'N/A')}`")
    lines.append(f"- **Index item count:** {summary.get('index_item_count', 0)}")
    lines.append(f"- **Item file count:** {summary.get('item_file_count', 0)}")
    lines.append("")

    # Pool State
    lines.append("## Pool State\n")

    # Status counts
    counts_by_status = summary.get("counts_by_status", {})
    if counts_by_status:
        lines.append("### Counts by Status\n")
        lines.append("| Status | Count |")
        lines.append("|--------|-------|")
        for status, count in sorted(counts_by_status.items()):
            lines.append(f"| {status} | {count} |")
        lines.append("")
    else:
        lines.append("### Counts by Status\n")
        lines.append("No items found.\n")

    # Layer counts
    counts_by_layer = summary.get("counts_by_layer", {})
    if counts_by_layer:
        lines.append("### Counts by Layer\n")
        lines.append("| Layer | Count |")
        lines.append("|-------|-------|")
        for layer, count in sorted(counts_by_layer.items()):
            lines.append(f"| {layer} | {count} |")
        lines.append("")

    # Lane counts
    counts_by_lane = summary.get("counts_by_lane", {})
    if counts_by_lane:
        lines.append("### Counts by Lane\n")
        lines.append("| Lane | Count |")
        lines.append("|------|-------|")
        for lane, count in sorted(counts_by_lane.items()):
            lines.append(f"| {lane} | {count} |")
        lines.append("")

    # Risk counts
    counts_by_risk = summary.get("counts_by_risk", {})
    if counts_by_risk:
        lines.append("### Counts by Risk\n")
        lines.append("| Risk Level | Count |")
        lines.append("|------------|-------|")
        for risk, count in sorted(counts_by_risk.items()):
            lines.append(f"| {risk} | {count} |")
        lines.append("")

    # Pilot counts
    pilot_counts = summary.get("pilot_counts", {})
    if pilot_counts:
        lines.append("### Pilot Counts\n")
        lines.append(f"- **Pilot artifacts:** {pilot_counts.get('pilot', 0)}")
        lines.append(f"- **Tasks:** {pilot_counts.get('task', 0)}")
        lines.append(f"- **is_pilot=True:** {pilot_counts.get('is_pilot_true', 0)}")
        lines.append(f"- **is_pilot=False:** {pilot_counts.get('is_pilot_false', 0)}")
        lines.append("")

    # Validate Gate
    lines.append("## Validate Gate\n")
    retry_summary = summary.get("retry_summary", {})
    lines.append(f"- **Total retry count:** {retry_summary.get('total_retry_count', 0)}")
    lines.append(f"- **Max retry count:** {retry_summary.get('max_retry_count', 0)}")
    lines.append(f"- **Items at or over max retry:** {retry_summary.get('items_at_or_over_max_retry', 0)}")
    lines.append(f"- **Items with retries:** {retry_summary.get('items_with_retries', 0)}")
    lines.append("")

    validate_summary = summary.get("validate_summary", {})
    lines.append(f"- **Total validation attempts:** {validate_summary.get('total_attempts', 0)}")
    validate_results = validate_summary.get("results", {})
    if validate_results:
        lines.append("### Validation Results\n")
        lines.append("| Result | Count |")
        lines.append("|--------|-------|")
        for result, count in sorted(validate_results.items()):
            lines.append(f"| {result} | {count} |")
        lines.append("")

    # Always emit a blank line before Governance Signals (even if validate_results was empty)
    lines.append("")

    # Governance Signals
    lines.append("## Governance Signals\n")
    governance_signals = summary.get("governance_signals", {})
    lines.append(f"- **L4 mandatory delegation:** {governance_signals.get('l4_mandatory_delegation_count', 0)}")
    lines.append(f"- **Pre-approval (HITL):** {governance_signals.get('pre_approval_count', 0)}")
    lines.append(f"- **Blocked items:** {governance_signals.get('blocked_count', 0)}")
    lines.append(f"- **Escalated items:** {governance_signals.get('escalated_count', 0)}")
    lines.append(f"- **Lane escalation triggered:** {governance_signals.get('lane_escalation_count', 0)}")
    lines.append(f"- **HITL required:** {governance_signals.get('hitl_required_count', 0)}")
    lines.append(f"- **QA required:** {governance_signals.get('qa_required_count', 0)}")
    lines.append("")

    # Integrity Warnings
    lines.append("## Integrity Warnings\n")
    integrity_warnings = summary.get("integrity_warnings", [])
    if integrity_warnings:
        for warning in integrity_warnings:
            path = warning.get("path", "unknown")
            message = warning.get("message", "unknown error")
            lines.append(f"- **{path}:** {message}")
        lines.append("")
    else:
        lines.append("No integrity warnings detected.\n")

    return "\n".join(lines)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Observability Report Generator — Phase v3.0 Observability & Monitoring MVP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script is READ-ONLY and does not modify any pool files.

Examples:
  python3 scripts/observability_report.py --format json
  python3 scripts/observability_report.py --format markdown
  python3 scripts/observability_report.py --pool-root /tmp/pool --pool-index /tmp/pool/pool.yaml --format json
        """
    )

    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--pool-root",
        type=Path,
        default=DEFAULT_POOL_ROOT,
        help=f"Pool root directory (default: {DEFAULT_POOL_ROOT})",
    )
    parser.add_argument(
        "--pool-index",
        type=Path,
        default=DEFAULT_POOL_INDEX,
        help=f"Pool index file path (default: {DEFAULT_POOL_INDEX})",
    )

    args = parser.parse_args()

    # Build summary (read-only operation)
    summary = build_summary(args.pool_root, args.pool_index)

    # Render output
    if args.format == "json":
        json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        markdown_output = render_markdown(summary)
        sys.stdout.write(markdown_output)


if __name__ == "__main__":
    main()
