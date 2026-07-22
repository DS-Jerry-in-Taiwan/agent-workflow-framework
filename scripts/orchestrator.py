#!/usr/bin/env python3
"""
v3.5 Autonomous Continuation Orchestrator — Workstream A

Pure-function integration layer that bridges pool items with the
continuation policy evaluator (scripts/continuation_policy.py).

Provides:
    find_active_pool_item(pool_index)          — locate the active (in_progress/picked) item
    build_active_plan_from_item(item)          — map pool item to active_plan dict
    build_continuation_context_from_item(item) — build context dict from pool item
    evaluate_message_against_item(message, item) — full continuation evaluation
    should_dispatch_continuation(decision)     — gate: can we dispatch?

Usage (library):
    from scripts.orchestrator import (
        find_active_pool_item,
        build_active_plan_from_item,
        build_continuation_context_from_item,
        evaluate_message_against_item,
        should_dispatch_continuation,
    )

This module is stdlib-only, does NOT modify:
    - scripts/intake_classify.py
    - scripts/lane_select.py
    - scripts/pool.py
    - config/routing_map_v1.json
"""

from __future__ import annotations

import sys
from pathlib import Path


# Ensure parent is on path so continuation_policy can be imported
# NOTE: This sys.path.insert works when orchestrator.py is run from the repo
# root (e.g., via `python3 scripts/orchestrator.py`).  In packaged/installed
# environments or unconventional runners, a different path strategy may be
# required.  If import errors occur despite this block, verify that the repo
# root is in sys.path or use PYTHONPATH.
_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR.parent))

from scripts.continuation_policy import decide_continuation, ASK_USER


# =============================================================================
# Constants
# =============================================================================

# Statuses that indicate a pool item is currently active
ACTIVE_STATUSES = ("in_progress", "picked", "qa_pending")

# Statuses that indicate a pool item is no longer active
TERMINAL_STATUSES = ("completed", "cancelled", "validated")


# =============================================================================
# 1. find_active_pool_item
# =============================================================================


def find_active_pool_item(pool_index: dict) -> dict | None:
    """
    Return the first pool index entry whose status indicates active work.

    Scans ``pool_index["items"]`` for entries with status in
    ``ACTIVE_STATUSES`` (``in_progress``, ``picked``, ``qa_pending``).

    Returns the index entry dict (which contains ``id``, ``status``,
    ``title``, ``layer``) or ``None`` if no active item is found.

    Backward compatible: old pool index entries without a ``status``
    field are skipped (safe fallback).
    """
    for entry in pool_index.get("items", []):
        status = entry.get("status")
        if status in ACTIVE_STATUSES:
            return entry
    return None


# =============================================================================
# 2. build_active_plan_from_item
# =============================================================================


def build_active_plan_from_item(item: dict) -> dict:
    """
    Build an ``active_plan`` dict from a full pool item.

    Maps pool item fields to the shape expected by
    ``continuation_policy.decide_continuation()``.

    Key mapping:
      - ``item["execution_contract"]["next_step"]`` →
        ``active_plan["next_planned_step"]``
      - ``item["continuation_policy"]`` (optional) →
        ``active_plan["auto_continue_allowed"]``,
        ``active_plan["checkpoint_complete"]``,
        ``active_plan["current_phase"]``

    Defaults:
      - ``auto_continue_allowed`` = ``False``
      - ``checkpoint_complete`` = ``False``

    Backward compatible: old items without ``continuation_policy``
    will use safe defaults without crashing.
    """
    cp = item.get("continuation_policy", {})

    return {
        "id": item.get("id"),
        "current_phase": (
            cp.get("current_phase")
            or item.get("status")
        ),
        "next_planned_step": (
            item.get("execution_contract", {}).get("next_step")
        ),
        "auto_continue_allowed": cp.get("auto_continue_allowed", False),
        "checkpoint_complete": cp.get("checkpoint_complete", False),
        "retry_count": item.get("retry_count", 0),
        "max_retry": item.get("max_retry", 3),
    }


# =============================================================================
# 3. build_continuation_context_from_item
# =============================================================================


def build_continuation_context_from_item(item: dict) -> dict:
    """
    Build a continuation ``context`` dict from a full pool item.

    Extracts fields that ``continuation_policy.check_must_stop_triggers``
    and ``decide_continuation`` understand:

      - ``layer``: from ``lane_decision.lane`` (mapped to short form)
      - ``is_l4_operation``: from ``lane_decision.l4_mandatory_delegation``
      - ``mandatory_delegation``: from ``lane_decision.l4_mandatory_delegation``
      - ``force_push``: from ``continuation_policy.context_flags.force_push``
      - ``secrets_involved``: from ``continuation_policy.context_flags.secrets_involved``
      - ``classifier_semantic_change``: from ``continuation_policy.context_flags.classifier_semantic_change``
      - ``uncovered_runtime_change``: from ``continuation_policy.context_flags.uncovered_runtime_change``
      - ``ambiguous_failure``: from ``continuation_policy.context_flags.ambiguous_failure``

    Backward compatible: safe defaults for all fields.
    """
    lane_decision = item.get("lane_decision", {})
    cp = item.get("continuation_policy", {})
    context_flags = cp.get("context_flags", {})

    lane = lane_decision.get("lane", "")
    l4_delegation = lane_decision.get("l4_mandatory_delegation", False)

    # Derive is_l4_operation from lane or delegation flag
    is_l4 = bool(l4_delegation) or ("L4" in lane)

    return {
        "layer": lane,
        "is_l4_operation": is_l4,
        "mandatory_delegation": l4_delegation,
        "force_push": context_flags.get("force_push", False),
        "secrets_involved": context_flags.get("secrets_involved", False),
        "classifier_semantic_change": context_flags.get(
            "classifier_semantic_change", False
        ),
        "uncovered_runtime_change": context_flags.get(
            "uncovered_runtime_change", False
        ),
        "ambiguous_failure": context_flags.get("ambiguous_failure", False),
    }


# =============================================================================
# 4. evaluate_message_against_item
# =============================================================================


def evaluate_message_against_item(
    message: str,
    item: dict | None,
) -> dict:
    """
    Full continuation evaluation: build plan + context from *item*, then
    call ``decide_continuation(message, active_plan, context)``.

    When *item* is ``None`` (no active pool item), passes ``None`` for
    both plan and context so the policy returns ``ask_user``.

    Returns the raw result from ``decide_continuation()`` with keys:
      - state, reason, next_action, human_required_reason,
        must_stop_triggers, matched_continuation_signal
    """
    if item is None:
        return decide_continuation(message, active_plan=None, context=None)

    active_plan = build_active_plan_from_item(item)
    context = build_continuation_context_from_item(item)
    return decide_continuation(message, active_plan, context)


# =============================================================================
# 5. should_dispatch_continuation
# =============================================================================


def should_dispatch_continuation(decision: dict) -> bool:
    """
    Gate function: can we dispatch a native task based on this decision?

    Returns ``True`` only for states where dispatch is safe:
      - ``auto_continue`` — safe to continue with next_action
      - ``mandatory_handoff`` — safe to handoff to Releaser

    Returns ``False`` for states that must NOT dispatch:
      - ``ask_user`` — need human input
      - ``report_only`` — informational only, no action
      - ``blocked`` — hard stop

    This enforces the invariants:
      - ``ask_user`` and ``blocked`` never dispatch
      - Only ``auto_continue`` or ``mandatory_handoff`` may dispatch
    """
    state = decision.get("state", ASK_USER)
    return state in ("auto_continue", "mandatory_handoff")


# =============================================================================
# Legacy / convenience alias
# =============================================================================

evaluate_message_against_pool = evaluate_message_against_item
"""Legacy alias for ``evaluate_message_against_item``."""
