#!/usr/bin/env python3
"""
Unit tests for scripts/input_contract_adapter.py — Phase v3.8 Contracts.

Covers:
- normalize_raw_input(): all 8 fields present, types correct, no brainstorming
- validate_contract(): positive/negative/edge cases
- CLI --input and --file modes
- Standalone constraint: no imports from scripts/*.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Import directly from the module (not via scripts/*.py)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))
# Load module directly without going through scripts package
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "input_contract_adapter",
    Path(__file__).parent.parent / "scripts" / "input_contract_adapter.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

normalize_raw_input = _mod.normalize_raw_input
validate_contract = _mod.validate_contract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_cli(*args: str) -> tuple[int, str, str]:
    """Run adapter CLI, return (exit_code, stdout, stderr)."""
    script = Path(__file__).parent.parent / "scripts" / "input_contract_adapter.py"
    result = subprocess.run(
        [sys.executable, str(script)] + list(args),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _build_valid_contract(**overrides) -> dict:
    """Return a valid full contract dict, with optional field overrides."""
    base = {
        "execution_contract": {
            "clarified_spec": "Fix threshold bug in classifier",
            "scope_boundary": {
                "in_scope": ["scripts/classifier.py"],
                "out_of_scope": ["src/"],
            },
            "success_criteria": ["Tests pass"],
            "validation_plan": ["Run pytest"],
            "risk_level": "MEDIUM",
            "recommended_layer": "L2_bug_fix",
            "next_step": "Debug and fix",
            "residual_ambiguity": [],
        }
    }
    ec = base["execution_contract"]
    for k, v in overrides.items():
        ec[k] = v
    return base


# ---------------------------------------------------------------------------
# Test: normalize_raw_input() — unit function tests
# ---------------------------------------------------------------------------

class TestNormalizeRawInput(unittest.TestCase):
    def _check_all_fields(self, contract: dict, expected_layer=None):
        """Assert all 8 required fields are present with correct types."""
        ec = contract
        self.assertIsInstance(ec["clarified_spec"], str)
        self.assertTrue(len(ec["clarified_spec"]) > 0)
        self.assertIsInstance(ec["scope_boundary"], dict)
        self.assertIsInstance(ec["scope_boundary"]["in_scope"], list)
        self.assertIsInstance(ec["scope_boundary"]["out_of_scope"], list)
        self.assertIsInstance(ec["success_criteria"], list)
        self.assertIsInstance(ec["validation_plan"], list)
        self.assertIn(ec["risk_level"], ["LOW", "MEDIUM", "HIGH"])
        self.assertIn(ec["recommended_layer"], [
            "L0_config_housekeeping", "L1_feature_dev", "L2_bug_fix",
            "L3_refactor", "L4_release",
        ])
        self.assertIsInstance(ec["next_step"], str)
        self.assertTrue(len(ec["next_step"]) > 0)
        self.assertIsInstance(ec["residual_ambiguity"], list)
        if expected_layer:
            self.assertEqual(ec["recommended_layer"], expected_layer)

    def test_input_creates_all_fields(self):
        """Test: --input 'fix bug in classifier' produces all 8 fields."""
        contract = normalize_raw_input("fix bug in classifier")
        self._check_all_fields(contract, expected_layer="L2_bug_fix")

    def test_input_empty_rejected(self):
        """Test: empty string raises ValueError."""
        with self.assertRaises(ValueError):
            normalize_raw_input("")
        with self.assertRaises(ValueError):
            normalize_raw_input("   ")

    def test_input_single_word(self):
        """Test: single-word input produces valid contract."""
        contract = normalize_raw_input("test")
        self._check_all_fields(contract)
        self.assertEqual(contract["recommended_layer"], "L1_feature_dev")  # default

    def test_input_unicode(self):
        """Test: unicode input is accepted and produces valid contract."""
        contract = normalize_raw_input("修復搜索錯誤")
        self._check_all_fields(contract)
        self.assertIn("修復搜索錯誤", contract["clarified_spec"])

    def test_input_long_text(self):
        """Test: very long input (>2000 chars) produces valid contract."""
        long_text = "fix " + ("bug " * 1000)
        contract = normalize_raw_input(long_text)
        self._check_all_fields(contract)

    def test_input_no_brainstorming(self):
        """Test: in_scope and out_of_scope are always empty lists (no PM brainstorming)."""
        contract = normalize_raw_input("please add a new feature to the system")
        self.assertEqual(contract["scope_boundary"]["in_scope"], [])
        self.assertEqual(contract["scope_boundary"]["out_of_scope"], [])
        self.assertEqual(contract["success_criteria"], [])

    def test_input_residual_ambiguity_set(self):
        """Test: residual_ambiguity always contains the raw-input note."""
        contract = normalize_raw_input("do something")
        self.assertTrue(len(contract["residual_ambiguity"]) > 0)
        self.assertIn(
            "Input was raw",
            contract["residual_ambiguity"][0],
        )

    def test_input_validation_plan_fallback(self):
        """Test: validation_plan gets generic entry for obvious fix/test inputs."""
        for text in ["fix bug", "test this", "error in code"]:
            contract = normalize_raw_input(text)
            self.assertIsInstance(contract["validation_plan"], list)

    def test_input_release_keywords(self):
        """Test: 'release v1.2.3' maps to L4_release."""
        contract = normalize_raw_input("release v1.2.3")
        self._check_all_fields(contract, expected_layer="L4_release")
        self.assertEqual(contract["risk_level"], "HIGH")
        self.assertEqual(contract["next_step"], "Delegate to agent-releaser")

    def test_input_cross_layer_tie_uses_higher_risk(self):
        """Test: equal keyword matches use routing map cross-layer dominance."""
        contract = normalize_raw_input("release version")
        self._check_all_fields(contract, expected_layer="L4_release")

    def test_input_config_keywords(self):
        """Test: 'update config' maps to L0_config_housekeeping."""
        contract = normalize_raw_input("update ruff.toml config")
        self._check_all_fields(contract, expected_layer="L0_config_housekeeping")
        self.assertEqual(contract["risk_level"], "LOW")

    def test_input_refactor_keywords(self):
        """Test: 'refactor module' maps to L3_refactor."""
        contract = normalize_raw_input("refactor the authentication module")
        self._check_all_fields(contract, expected_layer="L3_refactor")
        self.assertEqual(contract["risk_level"], "HIGH")


# ---------------------------------------------------------------------------
# Test: validate_contract() — unit function tests
# ---------------------------------------------------------------------------

class TestValidateContract(unittest.TestCase):
    def test_valid_full_contract(self):
        """Test: valid contract returns (True, [])."""
        valid = _build_valid_contract()
        ok, errors = validate_contract(valid)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_missing_execution_contract_key(self):
        """Test: missing 'execution_contract' key returns errors."""
        ok, errors = validate_contract({})
        self.assertFalse(ok)
        self.assertTrue(any("execution_contract" in e for e in errors))

    def test_missing_required_field(self):
        """Test: missing any required field returns error for that field."""
        for field in [
            "clarified_spec", "scope_boundary", "success_criteria",
            "validation_plan", "risk_level", "recommended_layer",
            "next_step", "residual_ambiguity",
        ]:
            with self.subTest(field=field):
                contract = _build_valid_contract()
                del contract["execution_contract"][field]
                ok, errors = validate_contract(contract)
                self.assertFalse(ok)
                self.assertTrue(any(field in e for e in errors))

    def test_invalid_risk_level(self):
        """Test: invalid risk_level returns error."""
        ok, errors = validate_contract(_build_valid_contract(risk_level="CRITICAL"))
        self.assertFalse(ok)
        self.assertTrue(any("risk_level" in e for e in errors))

    def test_invalid_recommended_layer(self):
        """Test: invalid recommended_layer returns error."""
        ok, errors = validate_contract(_build_valid_contract(recommended_layer="L5"))
        self.assertFalse(ok)
        self.assertTrue(any("recommended_layer" in e for e in errors))

    def test_empty_clarified_spec(self):
        """Test: empty clarified_spec returns error."""
        ok, errors = validate_contract(_build_valid_contract(clarified_spec=""))
        self.assertFalse(ok)
        self.assertTrue(any("clarified_spec" in e for e in errors))

    def test_empty_next_step(self):
        """Test: empty next_step returns error."""
        ok, errors = validate_contract(_build_valid_contract(next_step="  "))
        self.assertFalse(ok)
        self.assertTrue(any("next_step" in e for e in errors))

    def test_scope_boundary_not_dict(self):
        """Test: scope_boundary that is not a dict returns error."""
        ok, errors = validate_contract(_build_valid_contract(scope_boundary="not_a_dict"))
        self.assertFalse(ok)
        self.assertTrue(any("scope_boundary" in e for e in errors))

    def test_scope_boundary_missing_in_scope(self):
        """Test: scope_boundary without in_scope returns error."""
        contract = _build_valid_contract()
        del contract["execution_contract"]["scope_boundary"]["in_scope"]
        ok, errors = validate_contract(contract)
        self.assertFalse(ok)
        self.assertTrue(any("in_scope" in e for e in errors))

    def test_list_fields_must_be_lists(self):
        """Test: success_criteria / validation_plan / residual_ambiguity must be lists."""
        for field in ["success_criteria", "validation_plan", "residual_ambiguity"]:
            with self.subTest(field=field):
                ok, errors = validate_contract(_build_valid_contract(**{field: "not_a_list"}))
                self.assertFalse(ok)
                self.assertTrue(any(field in e for e in errors))

    def test_valid_contract_empty_lists_ok(self):
        """Test: empty lists are valid for list fields."""
        contract = _build_valid_contract(
            success_criteria=[],
            validation_plan=[],
            residual_ambiguity=[],
            scope_boundary={"in_scope": [], "out_of_scope": []},
        )
        ok, errors = validate_contract(contract)
        self.assertTrue(ok)
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Test: CLI — --input mode
# ---------------------------------------------------------------------------

class TestCLIInputMode(unittest.TestCase):
    def test_cli_input_basic(self):
        """Test: --input produces valid JSON with all fields."""
        code, stdout, stderr = _run_cli("--input", "fix bug in classifier")
        self.assertEqual(code, 0, f"stderr: {stderr}")
        parsed = json.loads(stdout)
        ec = parsed["execution_contract"]
        self.assertIsInstance(ec["clarified_spec"], str)
        self.assertIn(ec["risk_level"], ["LOW", "MEDIUM", "HIGH"])
        self.assertIn(ec["recommended_layer"], [
            "L0_config_housekeeping", "L1_feature_dev", "L2_bug_fix",
            "L3_refactor", "L4_release",
        ])

    def test_cli_input_empty(self):
        """Test: --input '' exits non-zero."""
        code, stdout, stderr = _run_cli("--input", "")
        self.assertNotEqual(code, 0)
        self.assertIn("empty", stderr.lower())

    def test_cli_input_release(self):
        """Test: --input 'release v1.2.3' outputs L4."""
        code, stdout, stderr = _run_cli("--input", "release v1.2.3")
        self.assertEqual(code, 0)
        parsed = json.loads(stdout)
        self.assertEqual(parsed["execution_contract"]["recommended_layer"], "L4_release")
        self.assertEqual(parsed["execution_contract"]["risk_level"], "HIGH")


# ---------------------------------------------------------------------------
# Test: CLI — --file mode
# ---------------------------------------------------------------------------

class TestCLIFileMode(unittest.TestCase):
    def _temp_json(self, data: dict) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        return path

    def test_cli_file_valid_passthrough(self):
        """Test: valid JSON file is passed through with raw text preserved (not re-formatted)."""
        valid = _build_valid_contract()
        path = self._temp_json(valid)
        try:
            with open(path, "r", encoding="utf-8") as f:
                original_raw = f.read()
            code, stdout, stderr = _run_cli("--file", path)
            self.assertEqual(code, 0, f"stderr: {stderr}")
            # stdout must be EXACTLY the original raw JSON string (no re-formatting)
            self.assertEqual(stdout, original_raw)
        finally:
            os.unlink(path)

    def test_cli_file_invalid_json(self):
        """Test: malformed JSON exits non-zero with 'invalid JSON'."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write("{ this is not json }")
        try:
            code, stdout, stderr = _run_cli("--file", path)
            self.assertNotEqual(code, 0)
            self.assertIn("invalid JSON", stderr)
        finally:
            os.unlink(path)

    def test_cli_file_missing_field(self):
        """Test: JSON missing required field exits non-zero."""
        contract = _build_valid_contract()
        del contract["execution_contract"]["risk_level"]
        path = self._temp_json(contract)
        try:
            code, stdout, stderr = _run_cli("--file", path)
            self.assertNotEqual(code, 0)
            self.assertIn("risk_level", stderr)
        finally:
            os.unlink(path)

    def test_cli_file_invalid_risk(self):
        """Test: invalid risk_level exits non-zero."""
        path = self._temp_json(_build_valid_contract(risk_level="CRITICAL"))
        try:
            code, stdout, stderr = _run_cli("--file", path)
            self.assertNotEqual(code, 0)
            self.assertIn("risk_level", stderr)
        finally:
            os.unlink(path)

    def test_cli_file_invalid_layer(self):
        """Test: invalid recommended_layer exits non-zero."""
        path = self._temp_json(_build_valid_contract(recommended_layer="L5"))
        try:
            code, stdout, stderr = _run_cli("--file", path)
            self.assertNotEqual(code, 0)
            self.assertIn("recommended_layer", stderr)
        finally:
            os.unlink(path)

    def test_cli_file_nonexistent(self):
        """Test: nonexistent file exits non-zero."""
        code, stdout, stderr = _run_cli("--file", "/nonexistent/path/to/file.json")
        self.assertNotEqual(code, 0)


# ---------------------------------------------------------------------------
# Test: routing_map_v1.json invariant
# ---------------------------------------------------------------------------

class TestRoutingInvariant(unittest.TestCase):
    def test_routing_map_v1_unchanged(self):
        """Test: routing_map_v1.json checksum is unchanged."""
        routing_map_path = Path(__file__).parent.parent / "config" / "routing_map_v1.json"
        import hashlib
        with open(routing_map_path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        EXPECTED = "6b6f3b8a19ae2d0b920e6ba1cf1cc614dbbb00656519afbf05fa12e9292cd34c"
        self.assertEqual(actual, EXPECTED)


# ---------------------------------------------------------------------------
# Test: adapter is standalone
# ---------------------------------------------------------------------------

class TestAdapterStandalone(unittest.TestCase):
    def test_no_scripts_imports(self):
        """Test: adapter does not import any existing scripts/*.py modules."""
        script_path = Path(__file__).parent.parent / "scripts" / "input_contract_adapter.py"
        with open(script_path) as f:
            source = f.read()
        # Check that no import or from references existing scripts
        existing_scripts = [
            "from scripts.lane_select",
            "from scripts.pool",
            "from scripts.intake_classify",
            "from scripts.observability_report",
            "from scripts.orchestrator",
            "from scripts.continuation_policy",
            "from scripts.runner_loop",
            "from scripts.agend_message_adapter",
            "from scripts.omo_dispatch_adapter",
        ]
        for line in existing_scripts:
            self.assertNotIn(line, source, f"Adapter must not import: {line}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
