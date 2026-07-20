#!/usr/bin/env python3
"""
Unit tests for scripts/runner_loop.py — v3.7 Stream A Runner Loop MVP.

Uses stdlib unittest only. Tests use tempfile.TemporaryDirectory() and
monkeypatch to isolate from real pool and runner state files.

Covers:
- Empty pool / no active item exits cleanly (0)
- Active item with auto_continue_allowed → dispatch, write back
- ask_user / blocked / report_only → no dispatch, exit 1
- L4 item → skip, exit 1
- max_items bound → exit 2
- hard timeout bound → exit 2
- last_decision written back to continuation_policy
- runner_state.json updated
- Pool CLI still works after runner writes
- observability report contains runner fields
- pool.py cmd_add creates last_loop_timestamp in continuation_policy
"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import scripts.pool as pool
import scripts.orchestrator as orchestrator
from scripts.runner_loop import (
    EXIT_CLEAN,
    EXIT_HANDLED,
    EXIT_BOUNDS,
    load_runner_state,
    save_runner_state,
    run_autonomous_pass,
)


# =============================================================================
# Helpers
# =============================================================================

def _write_json(path: Path, data: dict) -> None:
    """Write JSON data to file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_json(path: Path) -> dict:
    """Read and parse JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _init_pool(pool_root: Path) -> None:
    """Initialize pool directories and pool.yaml."""
    pool.POOL_ROOT = pool_root
    pool.POOL_INDEX = pool_root / "pool.yaml"
    pool.ensure_pool_dirs()
    pool.cmd_init()


def _write_pool_index(pool_root: Path, items: list) -> Path:
    """Create pool.yaml with given items."""
    pool_index = pool_root / "pool.yaml"
    _write_json(pool_index, {
        "version": "v1.0",
        "items": items,
        "updated_at": "2026-07-19T00:00:00Z",
    })
    return pool_index


def _make_active_item(
    item_id: str = "pool-test-001",
    status: str = "in_progress",
    lane: str = "L1_Standard",
    l4_mandatory_delegation: bool = False,
    qa_required: bool = True,
    auto_continue_allowed: bool = True,
    next_step: str = "Run QA validation",
    continuation_policy_extra: dict | None = None,
) -> dict:
    """Create a minimal active pool item dict."""
    cp = {
        "auto_continue_allowed": auto_continue_allowed,
        "checkpoint_complete": False,
        "current_phase": status,
        "last_decision": None,
        "last_loop_timestamp": None,
        "context_flags": {},
    }
    if continuation_policy_extra:
        cp.update(continuation_policy_extra)
    return {
        "id": item_id,
        "title": f"Test task {item_id}",
        "status": status,
        "execution_contract": {
            "next_step": next_step,
            "recommended_layer": "L1_feature_dev",
            "risk_level": "MEDIUM",
        },
        "classifier_result": {
            "final_layer": "L1_feature_dev",
        },
        "lane_decision": {
            "lane": lane,
            "l4_mandatory_delegation": l4_mandatory_delegation,
            "qa_required": qa_required,
            "hitl_required": False,
            "hitl_mode": "review",
            "escalation_triggered": False,
            "required_agents": ["Developer"],
        },
        "retry_count": 0,
        "max_retry": 3,
        "validate_history": [],
        "continuation_policy": cp,
    }


def _write_item_file(pool_root: Path, item: dict, status: str) -> Path:
    """Write item to correct subdirectory based on status."""
    subdir_map = {
        "pending": "pending",
        "picked": "active",
        "in_progress": "active",
        "qa_pending": "pending",
        "completed": "completed",
    }
    subdir = subdir_map.get(status, "pending")
    item_dir = pool_root / subdir
    item_dir.mkdir(parents=True, exist_ok=True)
    item_path = item_dir / f"{item['id']}.json"
    # Write item with the status field set to the provided status
    item_to_write = dict(item)
    item_to_write["status"] = status
    _write_json(item_path, item_to_write)
    return item_path


# =============================================================================
# Tests — Runner State I/O
# =============================================================================

class TestRunnerStateIO(unittest.TestCase):
    """load_runner_state / save_runner_state round-trip."""

    def test_load_missing_state_returns_defaults(self):
        """Missing runner_state.json returns v1 defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runner_state.json"
            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                state = load_runner_state()
                self.assertEqual(state["runner_iteration_count"], 0)
                self.assertIsNone(state["last_loop_timestamp"])
                self.assertEqual(state["version"], "v1")

    def test_save_and_load_state_round_trip(self):
        """Write then read matches the original data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runner_state.json"
            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                original = {
                    "runner_iteration_count": 5,
                    "last_loop_timestamp": "2026-07-20T12:00:00+00:00",
                    "version": "v1",
                }
                save_runner_state(original)
                loaded = load_runner_state()
                self.assertEqual(loaded["runner_iteration_count"], 5)
                self.assertEqual(loaded["last_loop_timestamp"], "2026-07-20T12:00:00+00:00")
                self.assertEqual(loaded["version"], "v1")

    def test_load_invalid_json_returns_defaults(self):
        """Corrupt runner_state.json returns defaults, no crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runner_state.json"
            state_path.write_text("{ invalid }", encoding="utf-8")
            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                state = load_runner_state()
                self.assertEqual(state["runner_iteration_count"], 0)
                self.assertIsNone(state["last_loop_timestamp"])


# =============================================================================
# Tests — run_autonomous_pass core logic
# =============================================================================

class TestRunAutonomousPass(unittest.TestCase):
    """run_autonomous_pass() exit codes and dispatch behavior."""

    def test_empty_pool_exits_zero(self):
        """🟢 Pool with no items → EXIT_CLEAN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_CLEAN)

    def test_no_active_item_exit_zero(self):
        """Pool with items but none active → EXIT_CLEAN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()
            # Add a completed item (terminal, not active)
            item = _make_active_item("pool-001", status="completed", auto_continue_allowed=True)
            _write_item_file(pool_root, item, "completed")
            idx = pool.load_pool_index()
            idx["items"].append({
                "id": "pool-001", "status": "completed", "title": "Done",
                "layer": "L1_feature_dev",
            })
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_CLEAN)

    def test_active_item_dispatches_and_writes_last_decision(self):
        """🟢 Active item with auto_continue_allowed → dispatch, last_decision written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-001"

            # Set up pool
            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            # Write active item
            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                auto_continue_allowed=True,
                next_step="Run QA validation",
                lane="L1_Standard",
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({
                "id": item_id, "status": "in_progress", "title": item["title"],
                "layer": "L1_feature_dev",
            })
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_CLEAN)

            # Verify last_decision written
            # qa_required=True → status transitions to qa_pending → pending/ subdir
            item_path = pool_root / "pending" / f"{item_id}.json"
            self.assertTrue(item_path.exists(), f"Item file not found at {item_path}")
            saved = _read_json(item_path)
            self.assertIsNotNone(saved["continuation_policy"]["last_decision"])
            self.assertEqual(saved["continuation_policy"]["last_decision"]["state"], "auto_continue")
            self.assertIsNotNone(saved["continuation_policy"]["last_loop_timestamp"])

    def test_last_decision_written_back(self):
        """🟢 Verify continuation_policy.last_decision has all required keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-002"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(item_id=item_id, status="in_progress", auto_continue_allowed=True)
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                run_autonomous_pass(pool_root=pool_root, message="continue")

            # QA transition moves to pending/ dir
            saved = _read_json(pool_root / "pending" / f"{item_id}.json")
            decision = saved["continuation_policy"]["last_decision"]
            for key in ["state", "reason", "next_action", "human_required_reason", "must_stop_triggers", "matched_continuation_signal"]:
                self.assertIn(key, decision, f"Missing key: {key}")

    def test_ask_user_no_dispatch(self):
        """🔴 Item that returns ask_user → no dispatch, EXIT_HANDLED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-003"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            # auto_continue_allowed=False → ask_user on "continue"
            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                auto_continue_allowed=False,
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_HANDLED)

    def test_blocked_no_dispatch(self):
        """🔴 Item with blocked state → no dispatch, EXIT_HANDLED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-004"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            # Item with force_push context flag → BLOCKED on push message
            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                auto_continue_allowed=True,
                continuation_policy_extra={"context_flags": {"force_push": True}},
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="push to main")
                self.assertEqual(result, EXIT_HANDLED)

    def test_report_only_no_dispatch(self):
        """🔴 Item with report_only state → no dispatch, EXIT_HANDLED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-005"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            # checkpoint_complete + no next_step → report_only
            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                auto_continue_allowed=True,
                next_step=None,
                continuation_policy_extra={"checkpoint_complete": True},
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_HANDLED)

    def test_l4_mandatory_handoff_no_dispatch(self):
        """🎯 L4 item → no dispatch, EXIT_HANDLED, L4 log emitted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-006"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            # L4 item with l4_mandatory_delegation=True
            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                lane="L4_Releaser",
                l4_mandatory_delegation=True,
                auto_continue_allowed=True,
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L4_release"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_HANDLED)

    def test_l4_in_lane_name_skipped(self):
        """🎯 Item with 'L4' in lane name → skipped, EXIT_HANDLED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-007"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                lane="L4_Releaser",
                l4_mandatory_delegation=False,
                auto_continue_allowed=True,
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L4_release"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_HANDLED)

    def test_max_items_zero_exits_bounds(self):
        """📏 max_items=0 → EXIT_BOUNDS immediately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-008"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(item_id=item_id, status="in_progress")
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                result = run_autonomous_pass(pool_root=pool_root, max_items=0, message="continue")
                self.assertEqual(result, EXIT_BOUNDS)

    def test_hard_timeout_exceeded(self):
        """📏 Elapsed time >= timeout → EXIT_BOUNDS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-009"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(item_id=item_id, status="in_progress", auto_continue_allowed=True)
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            # Mock time.time to always return a value that makes elapsed >= 0.001
            fake_time = [0.0]
            def fake_time_fn():
                fake_time[0] += 10.0  # each call adds 10 seconds
                return fake_time[0]

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path), \
                 patch("scripts.runner_loop.time.time", fake_time_fn):
                result = run_autonomous_pass(pool_root=pool_root, hard_timeout=5.0, message="continue")
                self.assertEqual(result, EXIT_BOUNDS)

    def test_runner_state_updated(self):
        """🟢 After a pass, runner_state.json has incremented iteration count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                # Seed initial state
                initial = {"runner_iteration_count": 2, "last_loop_timestamp": "2026-07-19T00:00:00+00:00", "version": "v1"}
                save_runner_state(initial)

                # Run pass on empty pool (clean exit)
                result = run_autonomous_pass(pool_root=pool_root, message="continue")
                self.assertEqual(result, EXIT_CLEAN)

                # Verify state was updated
                state = load_runner_state()
                self.assertEqual(state["runner_iteration_count"], 3)
                self.assertIsNotNone(state["last_loop_timestamp"])


# =============================================================================
# Tests — Status transition (qa_required)
# =============================================================================

class TestQaStatusTransition(unittest.TestCase):
    """QA-required items transition from in_progress to qa_pending on dispatch."""

    def test_in_progress_qa_required_transitions_to_qa_pending(self):
        """🟢 in_progress + qa_required=True → status changes to qa_pending after dispatch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-010"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                qa_required=True,
                auto_continue_allowed=True,
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                run_autonomous_pass(pool_root=pool_root, message="continue")

            # File should have moved to pending dir (qa_pending → pending subdir)
            item_path = pool_root / "pending" / f"{item_id}.json"
            self.assertTrue(item_path.exists(), f"Item should be in pending dir at {item_path}")
            saved = _read_json(item_path)
            self.assertEqual(saved["status"], "qa_pending")

    def test_in_progress_qa_not_required_keeps_status(self):
        """📏 in_progress + qa_required=False → status stays in_progress."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-011"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(
                item_id=item_id,
                status="in_progress",
                qa_required=False,
                auto_continue_allowed=True,
            )
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                run_autonomous_pass(pool_root=pool_root, message="continue")

            # File should stay in active dir
            item_path = pool_root / "active" / f"{item_id}.json"
            self.assertTrue(item_path.exists(), f"Item should stay in active dir at {item_path}")
            saved = _read_json(item_path)
            self.assertEqual(saved["status"], "in_progress")


# =============================================================================
# Tests — Pool CLI round-trip
# =============================================================================

class TestPoolCliRoundTrip(unittest.TestCase):
    """Pool CLI commands still work after runner writes to pool."""

    def test_pool_cli_list_still_works(self):
        """🟢 After runner pass, pool.py list still works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-012"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(item_id=item_id, status="in_progress", auto_continue_allowed=True)
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                run_autonomous_pass(pool_root=pool_root, message="continue")

            # pool.py list should not raise
            list_args = __import__("argparse").Namespace(status=None)
            pool.cmd_list(list_args)

    def test_pool_cli_status_still_works(self):
        """🟢 After runner pass, pool.py status still works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"
            item_id = "pool-013"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            item = _make_active_item(item_id=item_id, status="in_progress", auto_continue_allowed=True, qa_required=True)
            _write_item_file(pool_root, item, "in_progress")
            idx = pool.load_pool_index()
            idx["items"].append({"id": item_id, "status": "in_progress", "title": item["title"], "layer": "L1_feature_dev"})
            pool.save_pool_index(idx)

            with patch("scripts.runner_loop.RUNNER_STATE_PATH", state_path):
                run_autonomous_pass(pool_root=pool_root, message="continue")

            # pool.py status should not raise
            status_args = __import__("argparse").Namespace(task_id=item_id)
            pool.cmd_status(status_args)


# =============================================================================
# Tests — Observability integration
# =============================================================================

class TestObservabilityIntegration(unittest.TestCase):
    """Observability report contains runner fields."""

    def test_observability_report_has_runner_fields(self):
        """🟢 build_summary() result contains runner_iteration_count and last_loop_timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            # Write runner state to the path observability_report reads
            with patch("scripts.observability_report.RUNNER_STATE_PATH", state_path):
                _write_json(state_path, {
                    "runner_iteration_count": 7,
                    "last_loop_timestamp": "2026-07-20T15:30:00+00:00",
                    "version": "v1",
                })

                from scripts.observability_report import build_summary
                summary = build_summary(pool_root, pool.POOL_INDEX)
                self.assertIn("runner_iteration_count", summary)
                self.assertIn("last_loop_timestamp", summary)
                self.assertEqual(summary["runner_iteration_count"], 7)
                self.assertEqual(summary["last_loop_timestamp"], "2026-07-20T15:30:00+00:00")

    def test_observability_report_defaults_when_no_runner_state(self):
        """📏 build_summary() returns 0/None for runner fields when runner_state.json absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            state_path = Path(tmpdir) / "runner_state.json"

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            # Patch to a non-existent path so defaults are used
            with patch("scripts.observability_report.RUNNER_STATE_PATH", state_path):
                from scripts.observability_report import build_summary
                summary = build_summary(pool_root, pool.POOL_INDEX)
                self.assertEqual(summary["runner_iteration_count"], 0)
                self.assertIsNone(summary["last_loop_timestamp"])


# =============================================================================
# Tests — pool.py cmd_add creates last_loop_timestamp
# =============================================================================

class TestPoolCmdAddLastLoopTimestamp(unittest.TestCase):
    """pool.py cmd_add creates continuation_policy with last_loop_timestamp."""

    def test_title_based_add_has_last_loop_timestamp(self):
        """🟢 Title-based add creates continuation_policy.last_loop_timestamp=None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            args = __import__("argparse").Namespace(
                title="Test task",
                file=None,
                layer="L1_feature_dev",
                lane="L1_Standard",
                risk="MEDIUM",
                priority=999,
                pilot=False,
            )
            pool.cmd_add(args)

            idx = pool.load_pool_index()
            self.assertEqual(len(idx["items"]), 1)
            item_id = idx["items"][0]["id"]
            item = pool.load_item_file(item_id)
            self.assertIn("continuation_policy", item)
            self.assertIn("last_loop_timestamp", item["continuation_policy"])
            self.assertIsNone(item["continuation_policy"]["last_loop_timestamp"])

    def test_file_based_add_has_last_loop_timestamp(self):
        """🟢 File-based add creates continuation_policy.last_loop_timestamp=None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            task_file = Path(tmpdir) / "task.json"
            _write_json(task_file, {"title": "File task", "layer": "L2_bug_fix"})

            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            args = __import__("argparse").Namespace(
                title=None,
                file=str(task_file),
                layer=None,
                lane=None,
                risk=None,
                priority=None,
                pilot=False,
            )
            pool.cmd_add(args)

            idx = pool.load_pool_index()
            item_id = idx["items"][0]["id"]
            item = pool.load_item_file(item_id)
            self.assertIn("continuation_policy", item)
            self.assertIn("last_loop_timestamp", item["continuation_policy"])
            self.assertIsNone(item["continuation_policy"]["last_loop_timestamp"])


# =============================================================================
# Tests — CLI Interface
# =============================================================================

class TestCliInterface(unittest.TestCase):
    """CLI invocation produces correct exit codes."""

    def test_cli_empty_pool_exit_zero(self):
        """🟢 CLI with no active pool → exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            runner_state = Path(tmpdir) / "runner_state.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent.parent / "scripts" / "runner_loop.py"),
                    "--pool-root", str(pool_root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parent.parent)},
            )
            self.assertEqual(result.returncode, EXIT_CLEAN)

    def test_cli_help_prints_usage(self):
        """🟢 runner_loop.py --help exits 0 and prints usage."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "runner_loop.py"), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("max-items", result.stdout.lower())

    def test_cli_with_message_arg(self):
        """🟢 CLI --message flag is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool.POOL_ROOT = pool_root
            pool.POOL_INDEX = pool_root / "pool.yaml"
            pool.ensure_pool_dirs()
            pool.cmd_init()

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent.parent / "scripts" / "runner_loop.py"),
                    "--pool-root", str(pool_root),
                    "--message", "proceed",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            # Should exit cleanly (no active item)
            self.assertEqual(result.returncode, EXIT_CLEAN)


# =============================================================================
# main
# =============================================================================

if __name__ == "__main__":
    unittest.main()
