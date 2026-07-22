#!/usr/bin/env python3
"""
Input Contract Adapter — Phase 0 Input Normalization for AWF.

Normalizes raw natural-language input into a typed Execution Contract JSON,
or validates/passes-through an existing Execution Contract JSON file.

Modes:
    --input "raw text"   → normalize to Execution Contract JSON
    --file path/to/x.json → validate and pass through (exit 0 if valid, exit 1 if invalid)

No network calls, no LLM calls, no imports from existing scripts/*.py.
Stdlib only: json, sys, argparse, re.
"""

import argparse
import json
import sys
import re
from pathlib import Path
from typing import Tuple

# ---------------------------------------------------------------------------
# Canonical field names (match lane_select.py fixtures + pool.py defaults)
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS = [
    "clarified_spec",
    "scope_boundary",
    "success_criteria",
    "validation_plan",
    "risk_level",
    "recommended_layer",
    "next_step",
    "residual_ambiguity",
]
_VALID_RISK = ["LOW", "MEDIUM", "HIGH"]
_VALID_LAYERS = [
    "L0_config_housekeeping",
    "L1_feature_dev",
    "L2_bug_fix",
    "L3_refactor",
    "L4_release",
]

# ponytail: temporary inline keyword subset — must reconcile with routing_map_v1.json v2
_LAYER_KEYWORDS = {
    "L0_config_housekeeping": [
        "config", "setting", "version", "bump", "changelog", "ci",
        "workflow", "docs", "env", "Dockerfile", "requirements",
        "dependency", "upgrade", "ruff", "pyproject", "release.json",
    ],
    "L1_feature_dev": [
        "new", "add", "feature", "processor", "provider",
        "tavily", "gemini", "parallel", "scrape", "crawl",
        "prompt", "template", "pipeline", "plugin",
    ],
    "L2_bug_fix": [
        "fix", "bug", "error", "broken", "crash", "wrong",
        "false positive", "false negative", "threshold",
        "not working", "fails", "exception", "regression",
        "lint", "import", "path", "missing", "timeout",
    ],
    "L3_refactor": [
        "refactor", "restructure", "rename", "move",
        "extract", "abstract", "unify", "consolidate",
        "reorganize", "split", "technical debt", "clean up",
    ],
    "L4_release": [
        "release", "deploy", "tag", "version",
        "prod", "production", "merge", "v0.",
        "healthcheck", "CI/CD", "main", "dev",
    ],
}
_LAYER_NEXT_STEP = {
    "L0_config_housekeeping": "Implement config change",
    "L1_feature_dev": "Plan feature implementation",
    "L2_bug_fix": "Debug and fix",
    "L3_refactor": "Plan refactoring",
    "L4_release": "Delegate to agent-releaser",
}
_LAYER_RISK = {
    "L0_config_housekeeping": "LOW",
    "L1_feature_dev": "MEDIUM",
    "L2_bug_fix": "MEDIUM",
    "L3_refactor": "HIGH",
    "L4_release": "HIGH",
}


def _detect_layer(text: str) -> str:
    """Detect recommended_layer via keyword scan. Falls back to L1_feature_dev."""
    lower = text.lower()
    best_layer = "L1_feature_dev"
    best_score = 0
    for layer, keywords in _LAYER_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > best_score or (score == best_score and score > 0):
            best_score = score
            best_layer = layer
    return best_layer


def _build_validation_plan(text: str) -> list:
    """Add a generic validation entry for obvious fix/bug inputs."""
    lower = text.lower()
    if any(kw in lower for kw in ["fix", "bug", "test", "error", "broken", "fails"]):
        return ["Run full test suite"]
    return []


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def normalize_raw_input(text: str) -> dict:
    """
    Normalize raw natural-language text into an Execution Contract dict.

    No PM brainstorming — scope/success_criteria remain empty unless explicit.
    Residual ambiguity is always populated to signal human review needed.
    """
    text = text.strip()
    if not text:
        raise ValueError("Error: empty input")

    # Normalize to single-line clarified_spec
    clarified = re.sub(r"\s+", " ", text).strip()

    layer = _detect_layer(text)

    return {
        "clarified_spec": clarified,
        "scope_boundary": {"in_scope": [], "out_of_scope": []},
        "success_criteria": [],
        "validation_plan": _build_validation_plan(text),
        "risk_level": _LAYER_RISK[layer],
        "recommended_layer": layer,
        "next_step": _LAYER_NEXT_STEP[layer],
        "residual_ambiguity": [
            "Input was raw — contract needs human review for scope and criteria"
        ],
    }


def validate_contract(contract: dict) -> Tuple[bool, list]:
    """
    Validate an Execution Contract dict against the canonical 8-field schema.

    Returns:
        (True, [])  if valid
        (False, [error_strings])  if invalid
    """
    errors: list[str] = []

    if not isinstance(contract, dict):
        return False, ["Top-level contract must be a dict"]

    exec_contract = contract.get("execution_contract")
    if not isinstance(exec_contract, dict):
        errors.append("Missing or non-dict 'execution_contract' key")
        return False, errors

    # required fields
    for field in _REQUIRED_FIELDS:
        if field not in exec_contract:
            errors.append(f"Missing required field: {field}")

    ec = exec_contract  # shorthand

    if "clarified_spec" in ec:
        if not isinstance(ec["clarified_spec"], str) or not ec["clarified_spec"].strip():
            errors.append("'clarified_spec' must be a non-empty string")

    if "scope_boundary" in ec:
        sb = ec["scope_boundary"]
        if not isinstance(sb, dict):
            errors.append("'scope_boundary' must be a dict")
        else:
            if "in_scope" not in sb or not isinstance(sb.get("in_scope"), list):
                errors.append("'scope_boundary.in_scope' must be a list")
            if "out_of_scope" not in sb or not isinstance(sb.get("out_of_scope"), list):
                errors.append("'scope_boundary.out_of_scope' must be a list")

    for list_field in ("success_criteria", "validation_plan", "residual_ambiguity"):
        if list_field in ec and not isinstance(ec[list_field], list):
            errors.append(f"'{list_field}' must be a list")

    if "risk_level" in ec:
        if ec["risk_level"] not in _VALID_RISK:
            errors.append(
                f"'risk_level' must be one of {_VALID_RISK}, got: {ec['risk_level']}"
            )

    if "recommended_layer" in ec:
        if ec["recommended_layer"] not in _VALID_LAYERS:
            errors.append(
                f"'recommended_layer' must be one of {_VALID_LAYERS}, got: {ec['recommended_layer']}"
            )

    if "next_step" in ec:
        if not isinstance(ec["next_step"], str) or not ec["next_step"].strip():
            errors.append("'next_step' must be a non-empty string")

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="input_contract_adapter",
        description="Normalize raw input to Execution Contract, or validate a contract file.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input", dest="raw_text", metavar="TEXT",
        help="Raw natural-language input text to normalize"
    )
    group.add_argument(
        "--file", dest="file_path", metavar="PATH",
        help="Path to an Execution Contract JSON file to validate"
    )

    args = parser.parse_args()

    try:
        if args.raw_text is not None:
            contract_body = normalize_raw_input(args.raw_text)
            result = {"execution_contract": contract_body}
            print(json.dumps(result, indent=2, ensure_ascii=False))
            sys.exit(0)

        elif args.file_path is not None:
            path = Path(args.file_path)
            if not path.is_file():
                print(f"Error: file not found: {args.file_path}", file=sys.stderr)
                sys.exit(1)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_text = f.read()
                contract = json.loads(raw_text)  # validate via json.loads to allow trailing whitespace variations
            except json.JSONDecodeError as e:
                print(f"Error: invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)

            valid, errors = validate_contract(contract)
            if valid:
                # Pass through original file text unchanged (preserve formatting/content)
                print(raw_text, end="")
                sys.exit(0)
            else:
                for err in errors:
                    print(f"Error: {err}", file=sys.stderr)
                sys.exit(1)

    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
