#!/usr/bin/env python3
"""
Tests for scripts/omo_dispatch_adapter.py — v3.7 Stream C

Covers:
  - L4 rejection
  - Translation table (L0-L3)
  - Mode setting (always "omo")
  - Governance constraints per layer
  - Pool round-trip (title-based + file-based)
  - Backward compatibility
  - Observability mode counts
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from omo_dispatch_adapter import (
    OmOL4NotAllowedError,
    WorkflowDecisionContract,
    OmODispatchPayload,
    translate_layer_to_omo_category,
    build_dispatch_payload,
    contract_from_dict,
    validate_omo_dispatch,
)


# ---------------------------------------------------------------------------
# TestL4Rejection
# ---------------------------------------------------------------------------

class TestL4Rejection(unittest.TestCase):
    """L4 tasks must raise OmOL4NotAllowedError; non-L4 layers must not."""

    def test_l4_raises_exception(self):
        contract = WorkflowDecisionContract(
            item_id="test-l4",
            original_request="release prod tag v1.2.3",
            final_layer="L4_release",
            confidence=0.95,
            mode="direct",
            l4_mandatory_delegation=True,
            lane="L4_Releaser",
            required_agents=["agent-releaser"],
        )
        with self.assertRaises(OmOL4NotAllowedError) as ctx:
            build_dispatch_payload(contract)
        self.assertIn("L4", str(ctx.exception))

    def test_exception_message_mentions_l4(self):
        contract = WorkflowDecisionContract(
            item_id="test-l4",
            original_request="release",
            final_layer="L4_release",
            confidence=0.9,
            mode="direct",
            l4_mandatory_delegation=True,
            lane="L4_Releaser",
            required_agents=["agent-releaser"],
        )
        with self.assertRaises(OmOL4NotAllowedError) as ctx:
            build_dispatch_payload(contract)
        msg = str(ctx.exception)
        self.assertIn("L4", msg)
        self.assertIn("agent-releaser", msg)

    def test_l3_with_l4_delegation_flag_does_not_raise(self):
        """l4_mandatory_delegation=True on L3 must NOT trigger L4 rejection (layer takes precedence)."""
        contract = WorkflowDecisionContract(
            item_id="test-l3",
            original_request="refactor core module",
            final_layer="L3_refactor",
            confidence=0.75,
            mode="guarded",
            l4_mandatory_delegation=True,
            lane="L3_HighRisk",
            required_agents=["agent-developer"],
        )
        # Must NOT raise
        payload = build_dispatch_payload(contract)
        self.assertIsInstance(payload, OmODispatchPayload)
        self.assertEqual(payload.omo_category, "ultrabrain")

    def test_non_l4_does_not_raise(self):
        """All L0-L3 layers must pass without raising."""
        for layer in ["L0_config_housekeeping", "L1_feature_dev", "L2_bug_fix", "L3_refactor"]:
            with self.subTest(layer=layer):
                contract = WorkflowDecisionContract(
                    item_id=f"test-{layer}",
                    original_request=f"test {layer}",
                    final_layer=layer,
                    confidence=0.7,
                    mode="guarded",
                    l4_mandatory_delegation=False,
                    lane="L1_Standard",
                    required_agents=["Developer"],
                )
                payload = build_dispatch_payload(contract)
                self.assertIsInstance(payload, OmODispatchPayload)


# ---------------------------------------------------------------------------
# TestTranslationTable
# ---------------------------------------------------------------------------

class TestTranslationTable(unittest.TestCase):
    """Translation table: L0→quick, L1→deep, L2→quick, L3→ultrabrain."""

    def test_l0_translates_to_quick(self):
        contract = WorkflowDecisionContract(
            item_id="t0", original_request="update config", final_layer="L0_config_housekeeping",
            confidence=0.9, mode="direct", l4_mandatory_delegation=False,
            lane="L0_Fast_Track", required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertEqual(payload.omo_category, "quick")

    def test_l1_translates_to_deep(self):
        contract = WorkflowDecisionContract(
            item_id="t1", original_request="add feature", final_layer="L1_feature_dev",
            confidence=0.75, mode="guarded", l4_mandatory_delegation=False,
            lane="L1_Standard", required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertEqual(payload.omo_category, "deep")

    def test_l2_translates_to_quick(self):
        contract = WorkflowDecisionContract(
            item_id="t2", original_request="fix bug", final_layer="L2_bug_fix",
            confidence=0.8, mode="direct", l4_mandatory_delegation=False,
            lane="L2_QuickFix", required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertEqual(payload.omo_category, "quick")

    def test_l3_translates_to_ultrabrain(self):
        contract = WorkflowDecisionContract(
            item_id="t3", original_request="refactor", final_layer="L3_refactor",
            confidence=0.7, mode="guarded", l4_mandatory_delegation=False,
            lane="L3_HighRisk", required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertEqual(payload.omo_category, "ultrabrain")

    def test_unknown_layer_falls_back_to_deep(self):
        contract = WorkflowDecisionContract(
            item_id="t-unk", original_request="unknown task",
            final_layer="L99_unknown", confidence=0.5, mode="clarify",
            l4_mandatory_delegation=False, lane="L1_Standard",
            required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertEqual(payload.omo_category, "deep")


# ---------------------------------------------------------------------------
# TestModeSetting
# ---------------------------------------------------------------------------

class TestModeSetting(unittest.TestCase):
    """Payload runtime_mode must always be 'omo'."""

    def test_payload_always_has_omo_mode(self):
        contract = WorkflowDecisionContract(
            item_id="t", original_request="test", final_layer="L1_feature_dev",
            confidence=0.7, mode="guarded", l4_mandatory_delegation=False,
            lane="L1_Standard", required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertEqual(payload.runtime_mode, "omo")

    def test_all_layers_have_omo_mode(self):
        for layer in ["L0_config_housekeeping", "L1_feature_dev", "L2_bug_fix", "L3_refactor"]:
            with self.subTest(layer=layer):
                contract = WorkflowDecisionContract(
                    item_id=f"t-{layer}", original_request=f"test {layer}",
                    final_layer=layer, confidence=0.7, mode="guarded",
                    l4_mandatory_delegation=False, lane="L1_Standard",
                    required_agents=["Developer"],
                )
                payload = build_dispatch_payload(contract)
                self.assertEqual(payload.runtime_mode, "omo")


# ---------------------------------------------------------------------------
# TestGovernanceConstraints
# ---------------------------------------------------------------------------

class TestGovernanceConstraints(unittest.TestCase):
    """Governance constraints must include 'no_l4' always; L3 adds human_approval."""

    def test_no_l4_constraint_always_present(self):
        for layer in ["L0_config_housekeeping", "L1_feature_dev", "L2_bug_fix", "L3_refactor"]:
            with self.subTest(layer=layer):
                contract = WorkflowDecisionContract(
                    item_id=f"t-{layer}", original_request=f"test {layer}",
                    final_layer=layer, confidence=0.7, mode="guarded",
                    l4_mandatory_delegation=False, lane="L1_Standard",
                    required_agents=["Developer"],
                )
                payload = build_dispatch_payload(contract)
                self.assertIn("no_l4", payload.governance_constraints)

    def test_l3_has_human_approval(self):
        contract = WorkflowDecisionContract(
            item_id="t-l3", original_request="refactor", final_layer="L3_refactor",
            confidence=0.7, mode="guarded", l4_mandatory_delegation=False,
            lane="L3_HighRisk", required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertIn("requires_human_approval", payload.governance_constraints)
        # L0-L2 must NOT have requires_human_approval
        for layer in ["L0_config_housekeeping", "L1_feature_dev", "L2_bug_fix"]:
            c2 = WorkflowDecisionContract(
                item_id=f"t-{layer}", original_request=f"test {layer}",
                final_layer=layer, confidence=0.7, mode="guarded",
                l4_mandatory_delegation=False, lane="L1_Standard",
                required_agents=["Developer"],
            )
            p2 = build_dispatch_payload(c2)
            self.assertNotIn("requires_human_approval", p2.governance_constraints)

    def test_no_cross_mode_validate_present(self):
        contract = WorkflowDecisionContract(
            item_id="t", original_request="test", final_layer="L1_feature_dev",
            confidence=0.7, mode="guarded", l4_mandatory_delegation=False,
            lane="L1_Standard", required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertIn("no_cross_mode_validate", payload.governance_constraints)

    def test_unknown_layer_has_architect_review(self):
        contract = WorkflowDecisionContract(
            item_id="t-unk", original_request="unknown",
            final_layer="L99_unknown", confidence=0.5, mode="clarify",
            l4_mandatory_delegation=False, lane="L1_Standard",
            required_agents=["Developer"],
        )
        payload = build_dispatch_payload(contract)
        self.assertIn("requires_architect_review", payload.governance_constraints)


# ---------------------------------------------------------------------------
# TestValidateOmoDispatch
# ---------------------------------------------------------------------------

class TestValidateOmoDispatch(unittest.TestCase):
    """validate_omo_dispatch returns (True, []) for valid payload."""

    def test_valid_payload_passes(self):
        payload = OmODispatchPayload(
            item_id="t",
            omo_category="quick",
            original_request="test",
            runtime_mode="omo",
            governance_constraints=["no_l4", "no_cross_mode_validate"],
        )
        ok, errors = validate_omo_dispatch(payload)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_invalid_runtime_mode_fails(self):
        payload = OmODispatchPayload(
            item_id="t",
            omo_category="quick",
            original_request="test",
            runtime_mode="native",
            governance_constraints=["no_l4"],
        )
        ok, errors = validate_omo_dispatch(payload)
        self.assertFalse(ok)
        self.assertTrue(any("runtime_mode" in e for e in errors))

    def test_invalid_category_fails(self):
        payload = OmODispatchPayload(
            item_id="t",
            omo_category="invalid-category",
            original_request="test",
            runtime_mode="omo",
            governance_constraints=["no_l4"],
        )
        ok, errors = validate_omo_dispatch(payload)
        self.assertFalse(ok)
        self.assertTrue(any("omo_category" in e for e in errors))


# ---------------------------------------------------------------------------
# TestContractFromDict
# ---------------------------------------------------------------------------

class TestContractFromDict(unittest.TestCase):
    """contract_from_dict reconstructs a contract from a pool-item-like dict."""

    def test_full_pool_item(self):
        data = {
            "id": "pool-20260721-001",
            "title": "Add new feature",
            "classifier_result": {
                "final_layer": "L1_feature_dev",
                "confidence": 0.75,
                "mode": "guarded",
            },
            "lane_decision": {
                "l4_mandatory_delegation": False,
                "lane": "L1_Standard",
                "required_agents": ["Developer"],
            },
        }
        contract = contract_from_dict(data)
        self.assertEqual(contract.item_id, "pool-20260721-001")
        self.assertEqual(contract.original_request, "Add new feature")
        self.assertEqual(contract.final_layer, "L1_feature_dev")
        self.assertEqual(contract.confidence, 0.75)
        self.assertEqual(contract.mode, "guarded")
        self.assertFalse(contract.l4_mandatory_delegation)
        self.assertEqual(contract.lane, "L1_Standard")
        self.assertEqual(contract.required_agents, ["Developer"])

    def test_minimal_dict(self):
        data = {"id": "x"}
        contract = contract_from_dict(data)
        self.assertEqual(contract.item_id, "x")
        self.assertEqual(contract.original_request, "")
        self.assertEqual(contract.final_layer, "unknown")
        self.assertEqual(contract.confidence, 0.0)
        self.assertEqual(contract.mode, "clarify")
        self.assertFalse(contract.l4_mandatory_delegation)
        self.assertEqual(contract.lane, "unknown")
        self.assertEqual(contract.required_agents, [])


# ---------------------------------------------------------------------------
# TestPoolRoundTrip — imports pool.py (subprocess)
# ---------------------------------------------------------------------------

class TestPoolRoundTrip(unittest.TestCase):
    """Pool round-trip: --runtime-mode omo → item file has field."""

    def setUp(self):
        self.pool_root = tempfile.mkdtemp(prefix="pool_test_")
        self.pool_root_path = Path(self.pool_root)
        # Patch POOL_ROOT in pool.py before import
        import scripts.pool as pool_mod
        self._orig_pool_root = pool_mod.POOL_ROOT
        self._orig_pool_index = pool_mod.POOL_INDEX
        pool_mod.POOL_ROOT = self.pool_root_path
        pool_mod.POOL_INDEX = self.pool_root_path / "pool.yaml"
        # Ensure subdirs exist
        for d in pool_mod.SUBDIRS:
            (self.pool_root_path / d).mkdir(exist_ok=True)
        # Re-init index
        pool_mod.save_pool_index({"items": [], "version": "v1.0", "updated_at": None})

    def tearDown(self):
        import scripts.pool as pool_mod
        pool_mod.POOL_ROOT = self._orig_pool_root
        pool_mod.POOL_INDEX = self._orig_pool_index
        import shutil
        shutil.rmtree(self.pool_root, ignore_errors=True)

    def test_title_add_sets_runtime_mode_omo(self):
        """Title-based add with --runtime-mode omo stores runtime_mode=omo in item."""
        import scripts.pool as pool_mod
        import argparse
        args = argparse.Namespace(
            title="Test OmO task",
            file=None,
            layer="L1_feature_dev",
            lane="L1_Standard",
            risk="MEDIUM",
            priority=None,
            pilot=False,
            runtime_mode="omo",
        )
        pool_mod.cmd_add(args)

        # Load item file from pending/
        item_files = list((self.pool_root_path / "pending").glob("*.json"))
        self.assertEqual(len(item_files), 1)
        with open(item_files[0]) as f:
            item = json.load(f)
        self.assertEqual(item.get("runtime_mode"), "omo")

    def test_title_add_defaults_to_native(self):
        """Title-based add without flag defaults runtime_mode=native."""
        import scripts.pool as pool_mod
        import argparse
        args = argparse.Namespace(
            title="Test native task",
            file=None,
            layer="L2_bug_fix",
            lane="L2_QuickFix",
            risk="LOW",
            priority=None,
            pilot=False,
            runtime_mode="native",
        )
        pool_mod.cmd_add(args)

        item_files = list((self.pool_root_path / "pending").glob("*.json"))
        self.assertEqual(len(item_files), 1)
        with open(item_files[0]) as f:
            item = json.load(f)
        self.assertEqual(item.get("runtime_mode"), "native")

    def test_file_based_preserves_runtime_mode(self):
        """File-based add with runtime_mode=omo in source file preserves it."""
        import scripts.pool as pool_mod
        import argparse

        # Write a task file with runtime_mode=omo
        task_file = Path(self.pool_root) / "task.json"
        task_file.write_text(json.dumps({
            "id": "pool-file-test-001",
            "title": "File-based OmO task",
            "layer": "L1_feature_dev",
            "runtime_mode": "omo",
        }))

        args = argparse.Namespace(
            title=None,
            file=str(task_file),
            layer=None,
            lane=None,
            risk=None,
            priority=None,
            pilot=False,
            runtime_mode=None,
        )
        pool_mod.cmd_add(args)

        # Load item from pending/
        item_files = list((self.pool_root_path / "pending").glob("*.json"))
        self.assertEqual(len(item_files), 1)
        with open(item_files[0]) as f:
            item = json.load(f)
        self.assertEqual(item.get("runtime_mode"), "omo")

    def test_file_based_without_mode_defaults_native(self):
        """File-based add without runtime_mode defaults to native."""
        import scripts.pool as pool_mod
        import argparse

        task_file = Path(self.pool_root) / "task2.json"
        task_file.write_text(json.dumps({
            "id": "pool-file-test-002",
            "title": "File-based without mode",
            "layer": "L2_bug_fix",
            # no runtime_mode field
        }))

        args = argparse.Namespace(
            title=None,
            file=str(task_file),
            layer=None,
            lane=None,
            risk=None,
            priority=None,
            pilot=False,
            runtime_mode=None,
        )
        pool_mod.cmd_add(args)

        item_files = list((self.pool_root_path / "pending").glob("*.json"))
        self.assertEqual(len(item_files), 1)
        with open(item_files[0]) as f:
            item = json.load(f)
        self.assertEqual(item.get("runtime_mode"), "native")


# ---------------------------------------------------------------------------
# TestPoolBackwardCompat
# ---------------------------------------------------------------------------

class TestPoolBackwardCompat(unittest.TestCase):
    """Items without runtime_mode field default to 'native' in observability."""

    def setUp(self):
        self.pool_root = tempfile.mkdtemp(prefix="obs_test_")
        self.pool_root_path = Path(self.pool_root)
        for d in ["active", "pending", "blocked", "completed"]:
            (self.pool_root_path / d).mkdir(exist_ok=True)
        self.pool_index = self.pool_root_path / "pool.yaml"
        with open(self.pool_index, "w") as f:
            json.dump({"items": [], "version": "v1.0", "updated_at": None}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.pool_root, ignore_errors=True)

    def test_item_without_runtime_mode_defaults_native(self):
        """Observability counts items without runtime_mode as 'native'."""
        import scripts.observability_report as obs_mod

        # Write an old-style item without runtime_mode
        item_path = self.pool_root_path / "pending" / "old-item.json"
        item_path.write_text(json.dumps({
            "id": "old-item-001",
            "title": "Old task without mode",
            "status": "pending",
            "classifier_result": {"final_layer": "L1_feature_dev"},
            "lane_decision": {"lane": "L1_Standard"},
            "execution_contract": {"recommended_layer": "L1_feature_dev", "risk_level": "MEDIUM"},
            # no runtime_mode
        }))

        summary = obs_mod.build_summary(self.pool_root_path, self.pool_index)
        self.assertEqual(summary.get("counts_by_mode", {}).get("native"), 1)


# ---------------------------------------------------------------------------
# TestObservabilityModeCounts
# ---------------------------------------------------------------------------

class TestObservabilityModeCounts(unittest.TestCase):
    """Observability correctly counts native and omo items."""

    def setUp(self):
        self.pool_root = tempfile.mkdtemp(prefix="obs_count_test_")
        self.pool_root_path = Path(self.pool_root)
        for d in ["active", "pending", "blocked", "completed"]:
            (self.pool_root_path / d).mkdir(exist_ok=True)
        self.pool_index = self.pool_root_path / "pool.yaml"
        with open(self.pool_index, "w") as f:
            json.dump({"items": [], "version": "v1.0", "updated_at": None}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.pool_root, ignore_errors=True)

    def _write_item(self, item_id: str, runtime_mode: str | None, status: str = "pending"):
        item_path = self.pool_root_path / status / f"{item_id}.json"
        data = {
            "id": item_id,
            "title": f"Task {item_id}",
            "status": status,
            "classifier_result": {"final_layer": "L1_feature_dev"},
            "lane_decision": {"lane": "L1_Standard"},
            "execution_contract": {"recommended_layer": "L1_feature_dev", "risk_level": "MEDIUM"},
        }
        if runtime_mode is not None:
            data["runtime_mode"] = runtime_mode
        item_path.write_text(json.dumps(data))

    def test_observability_counts_modes(self):
        """counts_by_mode correctly separates native and omo items."""
        import scripts.observability_report as obs_mod

        self._write_item("item-native-1", "native")
        self._write_item("item-native-2", "native")
        self._write_item("item-omo-1", "omo")
        self._write_item("item-old-1", None)  # old item, no runtime_mode

        summary = obs_mod.build_summary(self.pool_root_path, self.pool_index)
        counts = summary.get("counts_by_mode", {})
        self.assertEqual(counts.get("native"), 3)   # 2 native + 1 without field
        self.assertEqual(counts.get("omo"), 1)

    def test_observability_markdown_includes_mode_section(self):
        """render_markdown includes '### Counts by Mode' when items exist."""
        import scripts.observability_report as obs_mod

        self._write_item("item-native-1", "native")
        self._write_item("item-omo-1", "omo")

        summary = obs_mod.build_summary(self.pool_root_path, self.pool_index)
        markdown = obs_mod.render_markdown(summary)
        self.assertIn("### Counts by Mode", markdown)
        self.assertIn("native", markdown)
        self.assertIn("omo", markdown)


# ---------------------------------------------------------------------------
# TestValidateGateChecks — adapter does NOT import classify/lane_select
# ---------------------------------------------------------------------------

class TestAdapterBoundary(unittest.TestCase):
    """Adapter must NOT import intake_classify.py or lane_select.py."""

    def test_adapter_does_not_import_intake_classify(self):
        """Verify omo_dispatch_adapter.py has no import of intake_classify."""
        adapter_path = Path(__file__).parent.parent / "scripts" / "omo_dispatch_adapter.py"
        content = adapter_path.read_text()
        # Check only actual import lines (not prose in docstrings)
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                self.assertNotIn(
                    "intake_classify", stripped,
                    f"Found intake_classify in import statement: {stripped}"
                )

    def test_adapter_does_not_import_lane_select(self):
        """Verify omo_dispatch_adapter.py has no import of lane_select."""
        adapter_path = Path(__file__).parent.parent / "scripts" / "omo_dispatch_adapter.py"
        content = adapter_path.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                self.assertNotIn(
                    "lane_select", stripped,
                    f"Found lane_select in import statement: {stripped}"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
