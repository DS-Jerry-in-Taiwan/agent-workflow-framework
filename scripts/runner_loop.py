#!/usr/bin/env python3
"""
v3.7 Runner Loop MVP — Stream A.
Single-pass autonomous continuation dispatcher.
One invocation = one pass over the pool. NOT a daemon.

Usage:
    python3 scripts/runner_loop.py
    python3 scripts/runner_loop.py --max-items 3 --timeout 120 --message "continue"

Library:
    from scripts.runner_loop import run_autonomous_pass
    run_autonomous_pass(pool_root=..., message="continue")  # -> exit_code
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.orchestrator as orchestrator
import scripts.pool as pool

# ---- constants ----
DEFAULT_MAX_ITEMS = 5
DEFAULT_MAX_ITERATIONS = 10
DEFAULT_HARD_TIMEOUT = 300  # seconds
DEFAULT_CONTINUATION_MESSAGE = "continue"
RUNNER_STATE_PATH = _REPO_ROOT / "docs" / "agent_context" / "runner_state.json"

# Exit codes
EXIT_CLEAN = 0    # no active item / pass completed
EXIT_HANDLED = 1  # handled stop: ask_user / blocked / L4 / report_only
EXIT_BOUNDS = 2   # max_items or hard_timeout exceeded


# ---- runner state persistence ----

def _default_state() -> dict:
    return {"runner_iteration_count": 0, "last_loop_timestamp": None, "version": "v1"}


def load_runner_state() -> dict:
    try:
        if RUNNER_STATE_PATH.exists():
            with open(RUNNER_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return _default_state()


def save_runner_state(state: dict) -> None:
    RUNNER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: write-then-rename; add flock if cross-process needed
    tmp = RUNNER_STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(RUNNER_STATE_PATH)


# ---- helpers ----

def _log(msg: str) -> None:
    sys.stderr.write(f"[runner] {msg}\n")
    sys.stderr.flush()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_l4_item(item: dict) -> bool:
    ld = item.get("lane_decision", {})
    return bool(ld.get("l4_mandatory_delegation", False) or "L4" in ld.get("lane", ""))


def _determine_next_status(item: dict) -> str | None:
    """Transition in_progress -> qa_pending when qa_required=True."""
    current = item.get("status", "")
    if item.get("lane_decision", {}).get("qa_required", False) and current in ("in_progress", "picked"):
        return pool.STATUS_QA_PENDING
    return None


# ---- core pass ----

def run_autonomous_pass(
    *,
    pool_root: Path | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    hard_timeout: float = DEFAULT_HARD_TIMEOUT,
    message: str = DEFAULT_CONTINUATION_MESSAGE,
) -> int:
    """
    Execute a single autonomous pass over the task pool.

    Returns:
        0 = EXIT_CLEAN   — no active item or pass completed
        1 = EXIT_HANDLED — handled stop (ask_user / blocked / L4)
        2 = EXIT_BOUNDS  — max_items or hard_timeout exceeded
    """
    start_time = time.time()
    runner_state = load_runner_state()

    # pool_root override via module-level monkey-patch
    if pool_root is not None:
        _orig_root = pool.POOL_ROOT
        _orig_idx = pool.POOL_INDEX
        pool.POOL_ROOT = pool_root
        pool.POOL_INDEX = pool_root / "pool.yaml"

    try:
        pool_index = pool.load_pool_index()
        active_entry = orchestrator.find_active_pool_item(pool_index)

        if active_entry is None:
            _log("no active item, exiting cleanly")
            _save_and_return(runner_state, EXIT_CLEAN)
            return EXIT_CLEAN

        item_id = active_entry["id"]
        item = pool.load_item_file(item_id)
        if item is None:
            _log(f"active entry {item_id} has no item file, exiting cleanly")
            _save_and_return(runner_state, EXIT_CLEAN)
            return EXIT_CLEAN

        now_iso = _now_iso()

        # ---- L4 pre-check ----
        if _is_l4_item(item):
            _log(f"L4 task {item_id} — mandatory handoff, skipping")
            # Secondary gate: malformed item where dispatch would be True but delegation is False
            decision = orchestrator.evaluate_message_against_item(message, item)
            if orchestrator.should_dispatch_continuation(decision):
                if not item.get("lane_decision", {}).get("l4_mandatory_delegation", False):
                    _log(f"malformed item {item_id}: mandatory_handoff without l4_mandatory_delegation, blocking")
                    _save_and_return(runner_state, EXIT_HANDLED)
                    return EXIT_HANDLED
            _save_and_return(runner_state, EXIT_HANDLED)
            return EXIT_HANDLED

        # ---- Evaluate continuation ----
        decision = orchestrator.evaluate_message_against_item(message, item)
        can_dispatch = orchestrator.should_dispatch_continuation(decision)

        if can_dispatch:
            agents = item.get("lane_decision", {}).get("required_agents", [])
            _log(f"dispatch {item_id} → [{','.join(agents)}] state={decision.get('state','unknown')}")

            # Write back continuation_policy fields
            cp = item.setdefault("continuation_policy", {})
            cp["last_decision"] = decision
            cp["last_loop_timestamp"] = now_iso

            # Status transition
            next_status = _determine_next_status(item)
            final_status = next_status if next_status else item.get("status", pool.STATUS_IN_PROGRESS)

            pool.create_item_file(item_id, item, final_status, remove_from_other=True)
            for entry in pool_index.get("items", []):
                if entry["id"] == item_id:
                    entry["status"] = final_status
                    break
            pool.save_pool_index(pool_index)

            # Bounds checks
            if time.time() - start_time >= hard_timeout:
                _log(f"hard_timeout_exceeded ({time.time() - start_time:.1f}s >= {hard_timeout}s)")
                _save_and_return(runner_state, EXIT_BOUNDS)
                return EXIT_BOUNDS

            if max_items <= 0:
                _log("max_items_exceeded (0)")
                _save_and_return(runner_state, EXIT_BOUNDS)
                return EXIT_BOUNDS

            _save_and_return(runner_state, EXIT_CLEAN)
            return EXIT_CLEAN

        else:
            _log(f"skip {item_id} reason={decision.get('state', 'unknown')}")
            _save_and_return(runner_state, EXIT_HANDLED)
            return EXIT_HANDLED

    finally:
        if pool_root is not None:
            pool.POOL_ROOT = _orig_root
            pool.POOL_INDEX = _orig_idx


def _save_and_return(state: dict, exit_code: int) -> None:
    new_state = {
        "runner_iteration_count": state.get("runner_iteration_count", 0) + 1,
        "last_loop_timestamp": _now_iso(),
        "version": "v1",
    }
    save_runner_state(new_state)


# ---- CLI ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="v3.7 Runner Loop MVP — Single-pass autonomous continuation dispatcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Exit codes:
  0  Clean — no active item or pass completed successfully
  1  Handled stop — ask_user / blocked / L4 / report_only
  2  Bounds exceeded — max_items or hard_timeout reached

Examples:
  python3 scripts/runner_loop.py
  python3 scripts/runner_loop.py --max-items 3 --timeout 120 --message "continue"
  python3 scripts/runner_loop.py --pool-root /tmp/pool --message "proceed"
""",
    )
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_HARD_TIMEOUT)
    parser.add_argument("--message", type=str, default=DEFAULT_CONTINUATION_MESSAGE)
    parser.add_argument("--pool-root", type=str, default=None)
    args = parser.parse_args()

    pool_root_override = Path(args.pool_root) if args.pool_root else None
    exit_code = run_autonomous_pass(
        pool_root=pool_root_override,
        max_items=args.max_items,
        max_iterations=args.max_iterations,
        hard_timeout=args.timeout,
        message=args.message,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
