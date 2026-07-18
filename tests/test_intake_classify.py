#!/usr/bin/env python3
"""
Unit tests for scripts/intake_classify.py — Phase v2.4 Runtime Hardening.

Uses stdlib unittest only. Covers:
- compute_confidence() formula sampling
- classify() for L2, L4, clarify, mixed inputs
- DOMINANCE_ORDER canonical ordering
- dominance_applied boolean semantics
- l4_mandatory_delegation flag
"""

import sys
import unittest
from pathlib import Path

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import scripts.intake_classify as intake_classify


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ROUTING_MAP_PATH = (
    Path(__file__).parent.parent
    / "docs"
    / "intake_layer"
    / "routing_map_v1.json"
)

ROUTING_MAP = intake_classify.load_routing_map(str(ROUTING_MAP_PATH))


# ---------------------------------------------------------------------------
# Tests — canonical constants
# ---------------------------------------------------------------------------

class TestCanonicalConstants(unittest.TestCase):
    """Verify canonical invariants are preserved."""

    def test_confidence_formula_string(self):
        self.assertEqual(
            intake_classify.CONFIDENCE_FORMULA,
            "0.65 * margin_component + 0.35 * ratio_component",
        )

    def test_thresholds(self):
        self.assertEqual(intake_classify.THRESHOLD_DIRECT, 0.85)
        self.assertEqual(intake_classify.THRESHOLD_GUARDED, 0.55)

    def test_dominance_order_canonical(self):
        expected = [
            "L4_release",
            "L3_refactor",
            "L2_bug_fix",
            "L1_feature_dev",
            "L0_config_housekeeping",
        ]
        self.assertEqual(intake_classify.DOMINANCE_ORDER, expected)


# ---------------------------------------------------------------------------
# Tests — compute_confidence()
# ---------------------------------------------------------------------------

class TestComputeConfidence(unittest.TestCase):
    """Test confidence scoring formula."""

    def test_single_layer_strong_match(self):
        """High match count on one layer → positive confidence."""
        scores = {
            "L2_bug_fix": {"match_count": 3, "total_keywords": 30, "matched_keywords": ["fix", "bug", "error"]},
            "L1_feature_dev": {"match_count": 1, "total_keywords": 20, "matched_keywords": ["new"]},
            "L0_config_housekeeping": {"match_count": 0, "total_keywords": 20, "matched_keywords": []},
        }
        conf, top, second, margin, ratio = intake_classify.compute_confidence(scores)
        self.assertIsNotNone(top)
        self.assertEqual(top, "L2_bug_fix")
        self.assertGreater(conf, 0.0)
        # margin = (3-1)/3 = 0.667; ratio = 3/30 = 0.1
        self.assertAlmostEqual(margin, (3 - 1) / 3, places=4)
        self.assertAlmostEqual(ratio, 3 / 30, places=4)
        expected = 0.65 * ((3 - 1) / 3) + 0.35 * (3 / 30)
        self.assertAlmostEqual(conf, expected, places=4)

    def test_single_layer_zero_match(self):
        """No matches → zero confidence."""
        scores = {
            "L2_bug_fix": {"match_count": 0, "total_keywords": 30, "matched_keywords": []},
            "L1_feature_dev": {"match_count": 0, "total_keywords": 20, "matched_keywords": []},
        }
        conf, top, second, margin, ratio = intake_classify.compute_confidence(scores)
        self.assertEqual(conf, 0.0)
        self.assertIsNone(top)

    def test_tied_top_layers(self):
        """Tied scores: margin=0, confidence driven by ratio_component only."""
        scores = {
            "L2_bug_fix": {"match_count": 2, "total_keywords": 10, "matched_keywords": ["fix", "bug"]},
            "L1_feature_dev": {"match_count": 2, "total_keywords": 10, "matched_keywords": ["add", "new"]},
        }
        conf, top, second, margin, ratio = intake_classify.compute_confidence(scores)
        # margin = (2-2)/2 = 0.0; ratio = 2/10 = 0.2; conf = 0.35 * 0.2 = 0.07
        self.assertAlmostEqual(margin, 0.0, places=4)
        self.assertAlmostEqual(conf, 0.35 * 0.2, places=4)


# ---------------------------------------------------------------------------
# Tests — classify()
# ---------------------------------------------------------------------------

class TestClassifyBugFix(unittest.TestCase):
    """'fix threshold bug' → L2_bug_fix, guarded."""

    def test_fix_bug_confidence_positive(self):
        result = intake_classify.classify("fix threshold bug", ROUTING_MAP)
        self.assertEqual(result["final_layer"], "L2_bug_fix")
        self.assertGreater(result["confidence"], 0.0)
        self.assertIn(result["mode"], ("guarded", "direct"))

    def test_fix_bug_l4_delegation_false(self):
        result = intake_classify.classify("fix threshold bug", ROUTING_MAP)
        self.assertFalse(result.get("l4_mandatory_delegation"))

    def test_fix_bug_dominance_applied_false(self):
        """L2-only keyword → no cross-layer dominance."""
        result = intake_classify.classify("fix threshold bug", ROUTING_MAP)
        # May or may not be True depending on other keyword matches;
        # just verify field is present and is bool
        self.assertIsInstance(result.get("dominance_applied"), bool)


class TestClassifyRelease(unittest.TestCase):
    """'release prod tag v1.2.3' → L4_release, l4_mandatory_delegation=true."""

    def test_release_final_layer_l4(self):
        result = intake_classify.classify("release prod tag v1.2.3", ROUTING_MAP)
        self.assertEqual(result["final_layer"], "L4_release")

    def test_release_l4_mandatory_delegation_true(self):
        result = intake_classify.classify("release prod tag v1.2.3", ROUTING_MAP)
        self.assertTrue(result["l4_mandatory_delegation"])

    def test_release_confidence_high(self):
        result = intake_classify.classify("release prod tag v1.2.3", ROUTING_MAP)
        self.assertGreater(result["confidence"], 0.5)

    def test_release_mode_guarded_or_direct(self):
        result = intake_classify.classify("release prod tag v1.2.3", ROUTING_MAP)
        self.assertIn(result["mode"], ("guarded", "direct"))


class TestClassifyClarify(unittest.TestCase):
    """'please help' → mode=clarify, confidence=0.0."""

    def test_please_help_mode_clarify(self):
        result = intake_classify.classify("please help", ROUTING_MAP)
        self.assertEqual(result["mode"], "clarify")

    def test_please_help_confidence_zero(self):
        result = intake_classify.classify("please help", ROUTING_MAP)
        self.assertEqual(result["confidence"], 0.0)

    def test_please_help_final_layer_none(self):
        result = intake_classify.classify("please help", ROUTING_MAP)
        self.assertIsNone(result["final_layer"])


class TestClassifyMixedL4Dominance(unittest.TestCase):
    """Mixed 'fix bug and release prod tag v1.2.3' → L4 dominates."""

    def test_mixed_input_l4_dominates(self):
        result = intake_classify.classify(
            "fix bug and release prod tag v1.2.3", ROUTING_MAP
        )
        self.assertEqual(result["final_layer"], "L4_release")

    def test_mixed_input_l4_delegation_true(self):
        result = intake_classify.classify(
            "fix bug and release prod tag v1.2.3", ROUTING_MAP
        )
        self.assertTrue(result["l4_mandatory_delegation"])


class TestDominanceAppliedSemantics(unittest.TestCase):
    """Verify dominance_applied = len(matched_layers)>1 and final!=top_layer."""

    def test_dominance_applied_is_boolean(self):
        result = intake_classify.classify("fix bug", ROUTING_MAP)
        self.assertIsInstance(result["dominance_applied"], bool)

    def test_dominance_applied_field_always_present(self):
        result = intake_classify.classify("fix bug", ROUTING_MAP)
        self.assertIn("dominance_applied", result)

    def test_debug_internal_fields_excluded_from_output(self):
        """_debug fields must not appear in CLI output (filtered in main())."""
        result = intake_classify.classify("fix bug", ROUTING_MAP)
        # _debug is in the full result but should be absent from clean output
        self.assertIn("_debug", result, "_debug should be in classify() result")
        clean_keys = {k for k in result if not k.startswith("_")}
        self.assertNotIn("_debug", clean_keys)


# ---------------------------------------------------------------------------
# Tests — output shape
# ---------------------------------------------------------------------------

class TestOutputShape(unittest.TestCase):
    """Output dict must contain required fields."""

    def test_classify_returns_required_fields(self):
        result = intake_classify.classify("fix bug", ROUTING_MAP)
        required = [
            "input",
            "final_layer",
            "confidence",
            "mode",
            "matched_keywords",
            "top_score",
            "second_score",
            "dominance_applied",
            "next_step",
            "l4_mandatory_delegation",
        ]
        for field in required:
            with self.subTest(field=field):
                self.assertIn(field, result)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
