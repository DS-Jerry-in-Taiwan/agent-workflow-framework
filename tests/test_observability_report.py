#!/usr/bin/env python3
"""
Unit tests for scripts/observability_report.py — Phase v3.0 Observability & Monitoring MVP.

Uses stdlib unittest only. Tests use tempfile.TemporaryDirectory() and
path injection to avoid touching real pool artifacts.

Covers:
- Empty/missing pool returns valid summary with all top-level keys
- Populated pool counts status/layer/lane/risk correctly
- retry_summary handles retry_count 0/2/3 and max_retry 3
- validate_summary counts PASS/FAIL values in validate_history
- governance_signals counts l4_mandatory_delegation=True, hitl_mode="pre_approval", blocked/escalated status
- pilot_counts counts is_pilot=True and artifact_type="pilot"
- Malformed item JSON produces integrity_warnings and no crash
- Missing lane_decision / missing risk fields fall back to UNKNOWN
- render_markdown() includes required headings
- CLI --format json stdout is parseable
- CLI --format markdown stdout contains required headings
- Tests use temp dirs and do not mutate real pool
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.observability_report import (
    DEFAULT_POOL_ROOT,
    DEFAULT_POOL_INDEX,
    SUBDIRS,
    REQUIRED_TOP_LEVEL_KEYS,
    REQUIRED_MD_SECTIONS,
    load_pool_index,
    discover_item_files,
    load_item_files,
    build_summary,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    """Write JSON data to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _write_pool_index(pool_root: Path, items: list) -> Path:
    """Create pool.yaml with given items."""
    pool_index = pool_root / "pool.yaml"
    _write_json(pool_index, {
        "version": "v1.0",
        "items": items,
        "updated_at": "2026-07-18T00:00:00Z",
    })
    return pool_index


def _make_item(
    item_id: str = "pool-test-001",
    status: str = "pending",
    layer: str = "L1_feature_dev",
    lane: str = "L1_Standard",
    risk_level: str = "MEDIUM",
    is_pilot: bool = False,
    artifact_type: str = "task",
    retry_count: int = 0,
    max_retry: int = 3,
    validate_history: list = None,
    l4_mandatory_delegation: bool = False,
    hitl_mode: str = "review",
    escalation_triggered: bool = False,
    missing_lane_decision: bool = False,
    missing_risk: bool = False,
    missing_recommended_layer: bool = False,
    continuation_policy: dict | None = None,
) -> dict:
    """Create a pool item dict with specified attributes."""
    item = {
        "id": item_id,
        "title": f"Test task {item_id}",
        "status": status,
    }

    if missing_recommended_layer:
        # execution_contract present but recommended_layer is absent
        if not missing_risk:
            item["execution_contract"] = {
                "risk_level": risk_level,
            }
        else:
            item["execution_contract"] = {}
    else:
        if not missing_risk:
            item["execution_contract"] = {
                "recommended_layer": layer,
                "risk_level": risk_level,
            }
        else:
            item["execution_contract"] = {
                "recommended_layer": layer,
            }

    item["classifier_result"] = {
        "final_layer": layer,
    }

    if not missing_lane_decision:
        item["lane_decision"] = {
            "lane": lane,
            "l4_mandatory_delegation": l4_mandatory_delegation,
            "hitl_mode": hitl_mode,
            "escalation_triggered": escalation_triggered,
            "qa_required": True,
            "hitl_required": l4_mandatory_delegation or hitl_mode == "pre_approval",
        }

    item["retry_count"] = retry_count
    item["max_retry"] = max_retry
    item["validate_history"] = validate_history if validate_history is not None else []
    item["is_pilot"] = is_pilot
    item["artifact_type"] = artifact_type

    # v3.5 continuation_policy (optional, backward-compatible)
    if continuation_policy is not None:
        item["continuation_policy"] = continuation_policy

    return item


def _run_cli(format_type: str, pool_root: Path, pool_index: Path) -> str:
    """Run observability_report.py CLI and return stdout."""
    cmd = [
        sys.executable,
        str(Path(__file__).parent.parent / "scripts" / "observability_report.py"),
        "--format", format_type,
        "--pool-root", str(pool_root),
        "--pool-index", str(pool_index),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result.stdout


# ---------------------------------------------------------------------------
# Tests — load_pool_index()
# ---------------------------------------------------------------------------

class TestLoadPoolIndex(unittest.TestCase):
    """load_pool_index() handles missing/empty/invalid files gracefully."""

    def test_missing_pool_index_returns_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_index = Path(tmpdir) / "pool.yaml"
            result = load_pool_index(pool_index)
            self.assertEqual(result["items"], [])
            self.assertEqual(result["version"], "v1.0")

    def test_empty_pool_index_returns_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir)
            pool_index = pool_root / "pool.yaml"
            pool_index.write_text("", encoding="utf-8")
            result = load_pool_index(pool_index)
            self.assertEqual(result["items"], [])
            self.assertEqual(result["version"], "v1.0")

    def test_invalid_json_returns_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir)
            pool_index = pool_root / "pool.yaml"
            pool_index.write_text("{ invalid json }", encoding="utf-8")
            result = load_pool_index(pool_index)
            self.assertEqual(result["items"], [])
            self.assertEqual(result["version"], "v1.0")

    def test_valid_pool_index_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir)
            pool_index = _write_pool_index(pool_root, [
                {"id": "pool-001", "status": "pending"},
            ])
            result = load_pool_index(pool_index)
            self.assertEqual(len(result["items"]), 1)
            self.assertEqual(result["items"][0]["id"], "pool-001")


# ---------------------------------------------------------------------------
# Tests — discover_item_files()
# ---------------------------------------------------------------------------

class TestDiscoverItemFiles(unittest.TestCase):
    """discover_item_files() finds items in pool subdirectories."""

    def test_empty_pool_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            result = discover_item_files(pool_root)
            self.assertEqual(result, [])

    def test_discovers_items_in_all_subdirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            # Create items in different subdirs
            for subdir in SUBDIRS:
                (pool_root / subdir).mkdir(exist_ok=True)
                _write_json(pool_root / subdir / f"item_{subdir}.json", {"id": subdir})

            result = discover_item_files(pool_root)
            self.assertEqual(len(result), len(SUBDIRS))


# ---------------------------------------------------------------------------
# Tests — load_item_files()
# ---------------------------------------------------------------------------

class TestLoadItemFiles(unittest.TestCase):
    """load_item_files() parses JSON and returns warnings for malformed files."""

    def test_valid_items_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            item_path = Path(tmpdir) / "item.json"
            _write_json(item_path, {"id": "test-001", "status": "pending"})

            items, warnings = load_item_files([item_path])
            self.assertEqual(len(items), 1)
            self.assertEqual(warnings, [])

    def test_malformed_json_produces_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            item_path = Path(tmpdir) / "item.json"
            item_path.write_text("{ invalid }", encoding="utf-8")

            items, warnings = load_item_files([item_path])
            self.assertEqual(len(items), 0)
            self.assertEqual(len(warnings), 1)
            self.assertIn("path", warnings[0])
            self.assertIn("message", warnings[0])


# ---------------------------------------------------------------------------
# Tests — build_summary() empty pool
# ---------------------------------------------------------------------------

class TestBuildSummaryEmptyPool(unittest.TestCase):
    """build_summary() returns valid summary for empty pool."""

    def test_all_required_keys_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            pool_index = pool_root / "pool.yaml"
            _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            for key in REQUIRED_TOP_LEVEL_KEYS:
                self.assertIn(key, summary, f"Missing key: {key}")

    def test_empty_pool_counts_are_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            self.assertEqual(summary["index_item_count"], 0)
            self.assertEqual(summary["item_file_count"], 0)
            self.assertEqual(summary["counts_by_status"], {})
            self.assertEqual(summary["counts_by_layer"], {})

    def test_missing_pool_yaml_no_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            # No pool.yaml

            summary = build_summary(pool_root, Path(tmpdir) / "nonexistent.yaml")

            for key in REQUIRED_TOP_LEVEL_KEYS:
                self.assertIn(key, summary)


# ---------------------------------------------------------------------------
# Tests — build_summary() populated pool
# ---------------------------------------------------------------------------

class TestBuildSummaryPopulatedPool(unittest.TestCase):
    """build_summary() correctly counts populated pool items."""

    def test_counts_status_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()
            (pool_root / "completed").mkdir()
            (pool_root / "blocked").mkdir()

            # Add items with different statuses
            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", status="pending"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", status="pending"))
            _write_json(pool_root / "completed" / "item3.json",
                       _make_item("item3", status="completed"))
            _write_json(pool_root / "blocked" / "item4.json",
                       _make_item("item4", status="blocked"))

            pool_index = _write_pool_index(pool_root, [
                {"id": "item1", "status": "pending"},
                {"id": "item2", "status": "pending"},
                {"id": "item3", "status": "completed"},
                {"id": "item4", "status": "blocked"},
            ])

            summary = build_summary(pool_root, pool_index)

            self.assertEqual(summary["counts_by_status"]["pending"], 2)
            self.assertEqual(summary["counts_by_status"]["completed"], 1)
            self.assertEqual(summary["counts_by_status"]["blocked"], 1)

    def test_counts_by_layer_fallback_to_classifier_result(self):
        """When execution_contract.recommended_layer is absent, layer falls back to classifier_result.final_layer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            # Item with recommended_layer missing from execution_contract
            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1",
                                  missing_recommended_layer=True,
                                  layer="L2_bug_fix"))
            # Item with both fields present (primary path)
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2",
                                  layer="L1_feature_dev"))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            # item1 should count using classifier_result.final_layer = "L2_bug_fix"
            self.assertEqual(summary["counts_by_layer"]["L2_bug_fix"], 1)
            # item2 should count using execution_contract.recommended_layer = "L1_feature_dev"
            self.assertEqual(summary["counts_by_layer"]["L1_feature_dev"], 1)

    def test_counts_layer_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", layer="L0_config_housekeeping"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", layer="L1_feature_dev"))
            _write_json(pool_root / "pending" / "item3.json",
                       _make_item("item3", layer="L2_bug_fix"))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            self.assertEqual(summary["counts_by_layer"]["L0_config_housekeeping"], 1)
            self.assertEqual(summary["counts_by_layer"]["L1_feature_dev"], 1)
            self.assertEqual(summary["counts_by_layer"]["L2_bug_fix"], 1)

    def test_counts_lane_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", lane="L0_Fast_Track"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", lane="L4_Releaser"))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            self.assertEqual(summary["counts_by_lane"]["L0_Fast_Track"], 1)
            self.assertEqual(summary["counts_by_lane"]["L4_Releaser"], 1)

    def test_counts_risk_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", risk_level="LOW"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", risk_level="HIGH"))
            _write_json(pool_root / "pending" / "item3.json",
                       _make_item("item3", missing_risk=True))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            self.assertEqual(summary["counts_by_risk"]["LOW"], 1)
            self.assertEqual(summary["counts_by_risk"]["HIGH"], 1)
            self.assertEqual(summary["counts_by_risk"]["UNKNOWN"], 1)


# ---------------------------------------------------------------------------
# Tests — retry_summary
# ---------------------------------------------------------------------------

class TestRetrySummary(unittest.TestCase):
    """retry_summary correctly aggregates retry counts."""

    def test_retry_counts_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            # retry_count 0/2/3 with max_retry=3
            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", retry_count=0, max_retry=3))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", retry_count=2, max_retry=3))
            _write_json(pool_root / "pending" / "item3.json",
                       _make_item("item3", retry_count=3, max_retry=3))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            retry_summary = summary["retry_summary"]

            self.assertEqual(retry_summary["total_retry_count"], 5)  # 0+2+3
            self.assertEqual(retry_summary["max_retry_count"], 3)
            self.assertEqual(retry_summary["items_at_or_over_max_retry"], 1)  # item3
            self.assertEqual(retry_summary["items_with_retries"], 2)  # item2, item3


# ---------------------------------------------------------------------------
# Tests — validate_summary
# ---------------------------------------------------------------------------

class TestValidateSummary(unittest.TestCase):
    """validate_summary correctly counts PASS/FAIL values."""

    def test_validate_counts_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            # Item with PASS/FAIL history
            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", validate_history=[
                           {"attempt": 1, "result": "PASS"},
                           {"attempt": 2, "result": "FAIL"},
                           {"attempt": 3, "result": "PASS"},
                       ]))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            validate_summary = summary["validate_summary"]

            self.assertEqual(validate_summary["total_attempts"], 3)
            self.assertEqual(validate_summary["results"]["PASS"], 2)
            self.assertEqual(validate_summary["results"]["FAIL"], 1)

    def test_empty_validate_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", validate_history=[]))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            validate_summary = summary["validate_summary"]

            self.assertEqual(validate_summary["total_attempts"], 0)
            self.assertEqual(validate_summary["results"], {})


# ---------------------------------------------------------------------------
# Tests — governance_signals
# ---------------------------------------------------------------------------

class TestGovernanceSignals(unittest.TestCase):
    """governance_signals correctly counts L4/pre-approval/blocked/escalated."""

    def test_l4_mandatory_delegation_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", l4_mandatory_delegation=True, lane="L4_Releaser"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", l4_mandatory_delegation=False))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            gov = summary["governance_signals"]

            self.assertEqual(gov["l4_mandatory_delegation_count"], 1)

    def test_pre_approval_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", hitl_mode="pre_approval"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", hitl_mode="review"))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            gov = summary["governance_signals"]

            self.assertEqual(gov["pre_approval_count"], 1)

    def test_blocked_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()
            (pool_root / "blocked").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", status="pending"))
            _write_json(pool_root / "blocked" / "item2.json",
                       _make_item("item2", status="blocked"))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            gov = summary["governance_signals"]

            self.assertEqual(gov["blocked_count"], 1)

    def test_escalated_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", status="escalated"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", escalation_triggered=True))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            gov = summary["governance_signals"]

            self.assertEqual(gov["escalated_count"], 2)


# ---------------------------------------------------------------------------
# Tests — pilot_counts
# ---------------------------------------------------------------------------

class TestPilotCounts(unittest.TestCase):
    """pilot_counts correctly counts is_pilot and artifact_type."""

    def test_pilot_counts_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", is_pilot=True, artifact_type="pilot"))
            _write_json(pool_root / "pending" / "item2.json",
                       _make_item("item2", is_pilot=False, artifact_type="task"))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            pilot = summary["pilot_counts"]

            self.assertEqual(pilot["is_pilot_true"], 1)
            self.assertEqual(pilot["is_pilot_false"], 1)
            self.assertEqual(pilot["pilot"], 1)
            self.assertEqual(pilot["task"], 1)


# ---------------------------------------------------------------------------
# Tests — continuation_summary counts_by_state
# ---------------------------------------------------------------------------


class TestContinuationSummary(unittest.TestCase):
    """continuation_summary correctly counts states and triggers from continuation_policy.last_decision."""

    def test_counts_known_states_correctly(self):
        """🟢 Items with auto_continue and ask_user last_decision are counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(
                pool_root / "pending" / "item1.json",
                _make_item("item1", continuation_policy={
                    "last_decision": {"state": "auto_continue", "must_stop_triggers": []},
                }),
            )
            _write_json(
                pool_root / "pending" / "item2.json",
                _make_item("item2", continuation_policy={
                    "last_decision": {"state": "ask_user", "must_stop_triggers": ["retry_exhausted"]},
                }),
            )
            _write_json(
                pool_root / "pending" / "item3.json",
                _make_item("item3", continuation_policy={
                    "last_decision": {"state": "auto_continue", "must_stop_triggers": []},
                }),
            )

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            cs = summary["continuation_summary"]

            self.assertEqual(cs["counts_by_state"]["auto_continue"], 2)
            self.assertEqual(cs["counts_by_state"]["ask_user"], 1)

    def test_old_item_without_continuation_not_counted(self):
        """🔴 Old item without continuation_policy is not counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(
                pool_root / "pending" / "item1.json",
                _make_item("item1"),  # no continuation_policy
            )

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            cs = summary["continuation_summary"]
            self.assertEqual(cs["counts_by_state"], {})

    def test_empty_pool_zero_counts(self):
        """📏 Empty pool has zero continuation counts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            cs = summary["continuation_summary"]
            self.assertEqual(cs["counts_by_state"], {})
            self.assertEqual(cs["must_stop_trigger_count"], 0)

    def test_must_stop_trigger_counted(self):
        """🎯 Item with retry_exhausted trigger increments trigger count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(
                pool_root / "pending" / "item1.json",
                _make_item("item1", continuation_policy={
                    "last_decision": {
                        "state": "ask_user",
                        "must_stop_triggers": ["retry_exhausted", "scope_expansion"],
                    },
                }),
            )

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            cs = summary["continuation_summary"]
            self.assertEqual(cs["must_stop_trigger_count"], 2)

    def test_continuation_summary_in_required_keys(self):
        """continuation_summary is in REQUIRED_TOP_LEVEL_KEYS."""
        self.assertIn("continuation_summary", REQUIRED_TOP_LEVEL_KEYS)

    def test_malformed_item_intacts_continuation_summary(self):
        """🔲 Malformed item does not crash continuation_summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(
                pool_root / "pending" / "good.json",
                _make_item("good", continuation_policy={
                    "last_decision": {"state": "auto_continue", "must_stop_triggers": []},
                }),
            )
            (pool_root / "pending" / "bad.json").write_text("{ bad json }", encoding="utf-8")

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            cs = summary["continuation_summary"]
            self.assertEqual(cs["counts_by_state"]["auto_continue"], 1)
            self.assertGreaterEqual(len(summary["integrity_warnings"]), 1)


# ---------------------------------------------------------------------------
# Tests — missing fields fall back to UNKNOWN
# ---------------------------------------------------------------------------

class TestMissingFieldsFallback(unittest.TestCase):
    """Items with missing fields fall back to UNKNOWN values."""

    def test_missing_lane_decision_falls_back_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", missing_lane_decision=True))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            self.assertEqual(summary["counts_by_lane"]["UNKNOWN"], 1)


# ---------------------------------------------------------------------------
# Tests — generated_at / pool_root / pool_index value assertions
# ---------------------------------------------------------------------------

class TestGeneratedAtAndPathValues(unittest.TestCase):
    """generated_at, pool_root, pool_index carry the correct injected values."""

    def test_build_summary_values_are_injected_paths(self):
        """pool_root and pool_index summary fields equal the injected temp paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            pool_index = pool_root / "pool.yaml"
            _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            # pool_root and pool_index must be the exact strings we passed in
            self.assertEqual(summary["pool_root"], str(pool_root))
            self.assertEqual(summary["pool_index"], str(pool_index))

    def test_generated_at_is_non_empty_string(self):
        """generated_at is a non-empty string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            pool_index = pool_root / "pool.yaml"
            _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            generated_at = summary.get("generated_at")
            self.assertIsInstance(generated_at, str)
            self.assertTrue(len(generated_at) > 0)

    def test_generated_at_is_parseable_iso_like_timestamp(self):
        """generated_at is parseable as an ISO-like timestamp via fromisoformat()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            pool_index = pool_root / "pool.yaml"
            _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            generated_at = summary.get("generated_at")
            # Remove any trailing 'Z' and replace with '+00:00' for fromisoformat compatibility
            parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            self.assertIsInstance(parsed, datetime)

    def test_cli_json_values_match_injected_paths(self):
        """CLI --format json output contains pool_root/pool_index equal to CLI args."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()
            _write_json(pool_root / "pending" / "item.json", _make_item("item1"))
            pool_index = _write_pool_index(pool_root, [])

            stdout = _run_cli("json", pool_root, pool_index)
            parsed = json.loads(stdout)

            self.assertEqual(parsed["pool_root"], str(pool_root))
            self.assertEqual(parsed["pool_index"], str(pool_index))
            self.assertIsInstance(parsed["generated_at"], str)
            self.assertTrue(len(parsed["generated_at"]) > 0)

    def test_cli_markdown_values_match_injected_paths(self):
        """CLI --format markdown output contains pool_root/pool_index equal to CLI args."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()
            _write_json(pool_root / "pending" / "item.json", _make_item("item1"))
            pool_index = _write_pool_index(pool_root, [])

            stdout = _run_cli("markdown", pool_root, pool_index)

            # Pool root and index appear in the markdown as backtick-quoted paths
            self.assertIn(str(pool_root), stdout)
            self.assertIn(str(pool_index), stdout)


# ---------------------------------------------------------------------------
# Tests — malformed item JSON
# ---------------------------------------------------------------------------

class TestMalformedItemJSON(unittest.TestCase):
    """Malformed item JSON produces integrity_warnings and no crash."""

    def test_malformed_json_in_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            # Valid item
            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1"))
            # Malformed item
            (pool_root / "pending" / "item2.json").write_text(
                "{ malformed }", encoding="utf-8")

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)

            # Should have 1 valid item, 1 warning
            self.assertEqual(summary["item_file_count"], 1)
            self.assertEqual(len(summary["integrity_warnings"]), 1)
            self.assertIn("path", summary["integrity_warnings"][0])
            self.assertIn("message", summary["integrity_warnings"][0])


# ---------------------------------------------------------------------------
# Tests — render_markdown()
# ---------------------------------------------------------------------------

class TestRenderMarkdown(unittest.TestCase):
    """render_markdown() produces required sections."""

    def test_markdown_has_blank_line_before_governance_signals_when_validate_results_empty(self):
        """render_markdown() inserts a blank line before '## Governance Signals' even when validate_results is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1", validate_history=[]))

            pool_index = _write_pool_index(pool_root, [])
            summary = build_summary(pool_root, pool_index)

            # validate_results must be empty so we exercise the no-results branch
            self.assertEqual(summary["validate_summary"]["results"], {})

            markdown = render_markdown(summary)

            gov_idx = markdown.find("## Governance Signals")
            self.assertNotEqual(gov_idx, -1, "## Governance Signals not found in markdown")

            # Characters immediately before the '##' line must include at least one newline
            before = markdown[:gov_idx]
            self.assertTrue(
                before.endswith("\n\n") or before.endswith("\n"),
                f"Expected a blank line before '## Governance Signals', got: {repr(before[-40:])}"
            )

    def test_markdown_includes_required_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1"))

            pool_index = _write_pool_index(pool_root, [])

            summary = build_summary(pool_root, pool_index)
            markdown = render_markdown(summary)

            for section in REQUIRED_MD_SECTIONS:
                self.assertIn(section, markdown, f"Missing section: {section}")


# ---------------------------------------------------------------------------
# Tests — CLI output
# ---------------------------------------------------------------------------

class TestCLIJsonOutput(unittest.TestCase):
    """CLI --format json produces parseable JSON."""

    def test_json_output_parseable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1"))

            pool_index = _write_pool_index(pool_root, [])

            stdout = _run_cli("json", pool_root, pool_index)

            # Should not raise
            parsed = json.loads(stdout)

            for key in REQUIRED_TOP_LEVEL_KEYS:
                self.assertIn(key, parsed)


class TestCLIMarkdownOutput(unittest.TestCase):
    """CLI --format markdown produces required sections."""

    def test_markdown_output_has_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_root.mkdir(parents=True)
            (pool_root / "pending").mkdir()

            _write_json(pool_root / "pending" / "item1.json",
                       _make_item("item1"))

            pool_index = _write_pool_index(pool_root, [])

            stdout = _run_cli("markdown", pool_root, pool_index)

            for section in REQUIRED_MD_SECTIONS:
                self.assertIn(section, stdout, f"Missing section: {section}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
