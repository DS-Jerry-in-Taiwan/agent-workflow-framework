#!/usr/bin/env python3
"""
Unit tests for scripts/lane_select.py — Phase v2.4 Runtime Hardening.

Uses stdlib unittest only. Covers:
- Every SAMPLE_FIXTURES output has non-empty string bypass_risk
- L4_RELEASE output has ZERO_BYPASS wording and l4_mandatory_delegation=True
- L0_Fast_Track eligible output has qa_required=False and eligibility wording
- L2_QuickFix and L2_Investigate have correct bypass_risk
- lane_selector() function signature unchanged
"""

import sys
import unittest
from pathlib import Path

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lane_select import (
    LANE_L0_FAST_TRACK,
    LANE_L1_STANDARD,
    LANE_L2_QUICK_FIX,
    LANE_L2_INVESTIGATE,
    LANE_L3_HIGH_RISK,
    LANE_L4_RELEASER,
    AGENT_RELEASER,
    SAMPLE_FIXTURES,
    lane_selector,
    check_l0_fast_track_eligibility,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bypass_ok(bypass_risk):
    """bypass_risk must be a non-empty string."""
    return isinstance(bypass_risk, str) and len(bypass_risk.strip()) > 0


# ---------------------------------------------------------------------------
# Tests — all lanes include bypass_risk
# ---------------------------------------------------------------------------

class TestAllLanesBypassRisk(unittest.TestCase):
    """Every lane_selector() return dict must include non-empty bypass_risk."""

    LANES_UNDER_TEST = [
        ("L0_Fast_Track", "L0_Fast_Track"),
        ("L2_QuickFix", "L2_QuickFix"),
        ("L2_Investigate", "L2_Investigate"),
        ("L4_RELEASE", "L4_RELEASE"),
        ("L1_Feature", "L1_Feature"),
        ("L3_Refactor", "L3_Refactor"),
    ]

    def test_each_sample_fixture_has_bypass_risk(self):
        for fixture_name, _ in self.LANES_UNDER_TEST:
            with self.subTest(fixture=fixture_name):
                fixture_data = SAMPLE_FIXTURES[fixture_name]
                decision = lane_selector(
                    fixture_data["execution_contract"],
                    fixture_data["classifier_result"],
                )
                self.assertIn(
                    "bypass_risk",
                    decision,
                    f"{fixture_name} missing bypass_risk key",
                )
                self.assertTrue(
                    _bypass_ok(decision["bypass_risk"]),
                    f"{fixture_name} bypass_risk={decision['bypass_risk']!r} is not non-empty string",
                )


# ---------------------------------------------------------------------------
# Tests — L4_RELEASE
# ---------------------------------------------------------------------------

class TestL4Releaser(unittest.TestCase):
    """L4_RELEASE fixture: correct lane, mandatory delegation, ZERO_BYPASS."""

    @classmethod
    def setUpClass(cls):
        fixture = SAMPLE_FIXTURES["L4_RELEASE"]
        cls.decision = lane_selector(
            fixture["execution_contract"],
            fixture["classifier_result"],
        )

    def test_lane_is_l4_releaser(self):
        self.assertEqual(self.decision["lane"], LANE_L4_RELEASER)

    def test_required_agents_contains_releaser(self):
        self.assertIn(AGENT_RELEASER, self.decision["required_agents"])

    def test_hitl_mode_is_pre_approval(self):
        self.assertEqual(self.decision["hitl_mode"], "pre_approval")

    def test_l4_mandatory_delegation_true(self):
        self.assertTrue(self.decision["l4_mandatory_delegation"])

    def test_qa_required_true(self):
        self.assertTrue(self.decision["qa_required"])

    def test_hitl_required_true(self):
        self.assertTrue(self.decision["hitl_required"])

    def test_bypass_risk_contains_zero_bypass(self):
        br = self.decision["bypass_risk"]
        self.assertTrue(
            "ZERO_BYPASS" in br or "mandatory" in br.lower(),
            f"L4 bypass_risk={br!r} does not contain ZERO_BYPASS or 'mandatory'",
        )


# ---------------------------------------------------------------------------
# Tests — L0_Fast_Track (eligible)
# ---------------------------------------------------------------------------

class TestL0FastTrackEligible(unittest.TestCase):
    """L0_Fast_Track eligible: qa_required=False, LOW bypass_risk."""

    @classmethod
    def setUpClass(cls):
        fixture = SAMPLE_FIXTURES["L0_Fast_Track"]
        cls.decision = lane_selector(
            fixture["execution_contract"],
            fixture["classifier_result"],
        )

    def test_lane_is_l0_fast_track(self):
        self.assertEqual(self.decision["lane"], LANE_L0_FAST_TRACK)

    def test_qa_required_false(self):
        self.assertFalse(self.decision["qa_required"])

    def test_hitl_required_false(self):
        self.assertFalse(self.decision["hitl_required"])

    def test_hitl_mode_auto_approve(self):
        self.assertEqual(self.decision["hitl_mode"], "auto_approve")

    def test_l4_mandatory_delegation_false(self):
        self.assertFalse(self.decision["l4_mandatory_delegation"])

    def test_bypass_risk_is_low(self):
        br = self.decision["bypass_risk"]
        self.assertTrue(
            "LOW" in br or "eligible" in br.lower() or "low" in br.lower(),
            f"L0 eligible bypass_risk={br!r} does not reference LOW/eligibility",
        )

    def test_bypass_risk_non_empty(self):
        self.assertTrue(_bypass_ok(self.decision["bypass_risk"]))


# ---------------------------------------------------------------------------
# Tests — L2 lanes
# ---------------------------------------------------------------------------

class TestL2QuickFix(unittest.TestCase):
    """L2_QuickFix: MEDIUM bypass_risk with QA regression wording."""

    @classmethod
    def setUpClass(cls):
        fixture = SAMPLE_FIXTURES["L2_QuickFix"]
        cls.decision = lane_selector(
            fixture["execution_contract"],
            fixture["classifier_result"],
        )

    def test_lane_is_quick_fix(self):
        self.assertEqual(self.decision["lane"], LANE_L2_QUICK_FIX)

    def test_qa_required_true(self):
        self.assertTrue(self.decision["qa_required"])

    def test_bypass_risk_non_empty(self):
        self.assertTrue(_bypass_ok(self.decision["bypass_risk"]))

    def test_bypass_risk_medium(self):
        br = self.decision["bypass_risk"]
        self.assertIn("MEDIUM", br, f"L2 bypass_risk={br!r} should be MEDIUM")


class TestL2Investigate(unittest.TestCase):
    """L2_Investigate: MEDIUM bypass_risk with debugger wording."""

    @classmethod
    def setUpClass(cls):
        fixture = SAMPLE_FIXTURES["L2_Investigate"]
        cls.decision = lane_selector(
            fixture["execution_contract"],
            fixture["classifier_result"],
        )

    def test_lane_is_investigate(self):
        self.assertEqual(self.decision["lane"], LANE_L2_INVESTIGATE)

    def test_escalation_triggered_true(self):
        self.assertTrue(self.decision["escalation_triggered"])

    def test_bypass_risk_non_empty(self):
        self.assertTrue(_bypass_ok(self.decision["bypass_risk"]))

    def test_bypass_risk_medium(self):
        br = self.decision["bypass_risk"]
        self.assertIn("MEDIUM", br, f"L2 Investigate bypass_risk={br!r} should be MEDIUM")


# ---------------------------------------------------------------------------
# Tests — L1 and L3
# ---------------------------------------------------------------------------

class TestL1Feature(unittest.TestCase):
    """L1_Feature: MEDIUM bypass_risk."""

    @classmethod
    def setUpClass(cls):
        fixture = SAMPLE_FIXTURES["L1_Feature"]
        cls.decision = lane_selector(
            fixture["execution_contract"],
            fixture["classifier_result"],
        )

    def test_lane_is_l1_standard(self):
        self.assertEqual(self.decision["lane"], LANE_L1_STANDARD)

    def test_bypass_risk_non_empty(self):
        self.assertTrue(_bypass_ok(self.decision["bypass_risk"]))

    def test_bypass_risk_medium(self):
        br = self.decision["bypass_risk"]
        self.assertIn("MEDIUM", br, f"L1 bypass_risk={br!r} should be MEDIUM")


class TestL3Refactor(unittest.TestCase):
    """L3_Refactor: HIGH bypass_risk, pre-approval required."""

    @classmethod
    def setUpClass(cls):
        fixture = SAMPLE_FIXTURES["L3_Refactor"]
        cls.decision = lane_selector(
            fixture["execution_contract"],
            fixture["classifier_result"],
        )

    def test_lane_is_l3_high_risk(self):
        self.assertEqual(self.decision["lane"], LANE_L3_HIGH_RISK)

    def test_qa_required_true(self):
        self.assertTrue(self.decision["qa_required"])

    def test_hitl_required_true(self):
        self.assertTrue(self.decision["hitl_required"])

    def test_hitl_mode_pre_approval(self):
        self.assertEqual(self.decision["hitl_mode"], "pre_approval")

    def test_l4_mandatory_delegation_false(self):
        self.assertFalse(self.decision["l4_mandatory_delegation"])

    def test_bypass_risk_non_empty(self):
        self.assertTrue(_bypass_ok(self.decision["bypass_risk"]))

    def test_bypass_risk_high(self):
        br = self.decision["bypass_risk"]
        self.assertIn("HIGH", br, f"L3 bypass_risk={br!r} should be HIGH")


# ---------------------------------------------------------------------------
# Tests — check_l0_fast_track_eligibility
# ---------------------------------------------------------------------------

class TestCheckL0FastTrackEligibility(unittest.TestCase):
    """Unit tests for the eligibility checker."""

    def test_eligible_fixture_is_eligible(self):
        fixture = SAMPLE_FIXTURES["L0_Fast_Track"]
        eligible, reasons = check_l0_fast_track_eligibility(
            fixture["execution_contract"],
            fixture["classifier_result"],
        )
        self.assertTrue(eligible)
        self.assertEqual(reasons, [])

    def test_non_low_risk_not_eligible(self):
        contract = {
            "risk_level": "HIGH",
            "scope_boundary": {"in_scope": ["README.md"], "out_of_scope": []},
        }
        classifier = {"confidence": 0.9, "conflict_status": "aligned"}
        eligible, reasons = check_l0_fast_track_eligibility(contract, classifier)
        self.assertFalse(eligible)
        self.assertIn("E1", reasons[0])

    def test_low_confidence_not_eligible(self):
        contract = {
            "risk_level": "LOW",
            "scope_boundary": {"in_scope": ["README.md"], "out_of_scope": ["src/"]},
        }
        classifier = {"confidence": 0.5, "conflict_status": "aligned"}
        eligible, reasons = check_l0_fast_track_eligibility(contract, classifier)
        self.assertFalse(eligible)
        self.assertTrue(any("E4" in r for r in reasons))

    def test_involves_src_not_eligible(self):
        contract = {
            "risk_level": "LOW",
            "scope_boundary": {"in_scope": ["src/quality/checks.py"], "out_of_scope": []},
        }
        classifier = {"confidence": 0.9, "conflict_status": "aligned"}
        eligible, reasons = check_l0_fast_track_eligibility(contract, classifier)
        self.assertFalse(eligible)
        self.assertTrue(any("E3" in r for r in reasons))

    def test_conflict_status_not_eligible(self):
        """E5: conflict_status not aligned -> not eligible."""
        contract = {
            "risk_level": "LOW",
            "scope_boundary": {"in_scope": ["README.md"], "out_of_scope": ["src/"]},
        }
        classifier = {"confidence": 0.9, "conflict_status": "conflict_reviewed"}
        eligible, reasons = check_l0_fast_track_eligibility(contract, classifier)
        self.assertFalse(eligible)
        self.assertTrue(any("E5" in r for r in reasons))

    def test_prod_config_not_eligible(self):
        """E6: involves prod config files -> not eligible."""
        contract = {
            "risk_level": "LOW",
            "scope_boundary": {"in_scope": ["prod.config"], "out_of_scope": ["src/"]},
        }
        classifier = {"confidence": 0.9, "conflict_status": "aligned"}
        eligible, reasons = check_l0_fast_track_eligibility(contract, classifier)
        self.assertFalse(eligible)
        self.assertTrue(any("E6" in r for r in reasons))

    def test_release_adjacent_not_eligible(self):
        """E7: involves release-adjacent config -> not eligible."""
        contract = {
            "risk_level": "LOW",
            "scope_boundary": {"in_scope": [".github/workflows/deploy-prod.yml"], "out_of_scope": ["src/"]},
        }
        classifier = {"confidence": 0.9, "conflict_status": "aligned"}
        eligible, reasons = check_l0_fast_track_eligibility(contract, classifier)
        self.assertFalse(eligible)
        self.assertTrue(any("E7" in r for r in reasons))


# ---------------------------------------------------------------------------
# Tests — fallback lane
# ---------------------------------------------------------------------------

class TestFallbackLane(unittest.TestCase):
    """Unknown layer → fallback L1 Standard with MEDIUM bypass_risk."""

    def test_unknown_layer_fallback_l1(self):
        decision = lane_selector(
            {"risk_level": "MEDIUM"},  # empty contract
            {"final_layer": "UNKNOWN_LAYER", "confidence": 0.0, "conflict_status": "aligned"},
        )
        self.assertEqual(decision["lane"], LANE_L1_STANDARD)
        self.assertTrue(_bypass_ok(decision["bypass_risk"]))
        self.assertIn("MEDIUM", decision["bypass_risk"])


# ---------------------------------------------------------------------------
# Tests — L0 escalation
# ---------------------------------------------------------------------------

class TestL0Escalation(unittest.TestCase):
    """L0 layer not eligible → escalated to L1 with MEDIUM bypass_risk."""

    def test_l0_not_eligible_escalates(self):
        # L0 layer but confidence < 0.85 → not eligible
        decision = lane_selector(
            {
                "risk_level": "LOW",
                "scope_boundary": {"in_scope": ["README.md"], "out_of_scope": []},
            },
            {
                "final_layer": "L0_config_housekeeping",
                "confidence": 0.5,  # too low
                "conflict_status": "aligned",
            },
        )
        self.assertEqual(decision["lane"], LANE_L1_STANDARD)
        self.assertTrue(decision["escalation_triggered"])
        self.assertTrue(_bypass_ok(decision["bypass_risk"]))
        self.assertIn("MEDIUM", decision["bypass_risk"])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
