#!/usr/bin/env python3
"""
Unit tests for scripts/agend_message_adapter.py — v3.3 Phase 4 Minimal Adapter Spike.

Uses stdlib unittest only. Covers:
- validate_workflow_decision() for valid, missing fields, unknown lane, confidence range
- governance_pre_dispatch() for clarify, L4 non-Releaser, safe dispatch
- format_dispatch_payload() for L0/L1/L2/L3/L4, metadata shape
- Integration: validate → governance → format pipeline for L4 Releaser
"""

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.agend_message_adapter import (
    KNOWN_LAYERS,
    KNOWN_LANES,
    AGENT_RELEASER,
    L4_LANE,
    validate_workflow_decision,
    governance_pre_dispatch,
    format_dispatch_payload,
    WorkflowEventWriter,
    validate_event_line,
    validate_event_file,
    EVENT_TYPES,
)

# ---------------------------------------------------------------------------
# Sample minimal valid decision contracts for each lane
# ---------------------------------------------------------------------------

SAMPLE_REQUEST_ID = "workflow-20260719-001"

# Reusable valid classifier result (non-L4 base)
L1_CLASSIFIER = {
    "final_layer": "L1_feature_dev",
    "confidence": 0.88,
    "mode": "guarded",
    "matched_keywords": {"L1_feature_dev": ["add", "new", "feature"]},
    "top_score": 3,
    "second_score": 1,
    "dominance_applied": False,
    "next_step": "Architect reviews design",
    "l4_mandatory_delegation": False,
}

L1_EXECUTION_CONTRACT = {
    "clarified_spec": "Add new search provider",
    "scope_boundary": {
        "in_scope": ["src/services/search/"],
        "out_of_scope": ["UI changes"],
    },
    "success_criteria": ["New provider integrated", "Tests pass"],
    "validation_plan": ["Unit tests", "Integration tests"],
    "risk_level": "MEDIUM",
    "recommended_layer": "L1_feature_dev",
    "next_step": "Architect reviews design",
    "residual_ambiguity": [],
}

L1_LANE_DECISION = {
    "lane": "L1_Standard",
    "escalation_triggered": False,
    "escalation_reason": None,
    "required_agents": ["Architect", "Developer", "QA"],
    "qa_required": True,
    "hitl_required": False,
    "hitl_mode": "review",
    "l4_mandatory_delegation": False,
    "bypass_risk": "MEDIUM — feature workflow requires QA and Architect review",
}

L1_GOVERNANCE = {
    "forbidden_actions": [],
    "required_handoff": None,
    "human_approval_required": False,
    "safe_to_dispatch": True,
    "safe_stop_reason": None,
}

L1_ADAPTER_DISPATCH = {
    "target_runtime": "native",
    "target_agent": "agent-developer",
    "correlation_id": "workflow-20260719-001",
    "dispatch_allowed": True,
    "dispatch_payload_kind": "task",
}


def _make_valid_decision(overrides: dict | None = None) -> dict:
    """Build a minimally valid L1 decision contract, optionally overridden."""
    decision = {
        "contract_version": "v3.3-wave1",
        "request_id": SAMPLE_REQUEST_ID,
        "created_at": "2026-07-19T00:00:00Z",
        "original_request": "Add new search provider",
        "classifier_result": dict(L1_CLASSIFIER),
        "execution_contract": dict(L1_EXECUTION_CONTRACT),
        "lane_decision": dict(L1_LANE_DECISION),
        "governance": dict(L1_GOVERNANCE),
        "adapter_dispatch": dict(L1_ADAPTER_DISPATCH),
        "evidence": {
            "source_files_read": ["scripts/intake_classify.py"],
            "validation_commands": ["python3 -m pytest tests/"],
            "event_refs": ["evt-20260719-001"],
            "audit_log_ref": None,
        },
    }
    if overrides:
        # Shallow merge at top level
        for key, val in overrides.items():
            decision[key] = val
    return decision


# ===========================================================================
# Test: validate_workflow_decision
# ===========================================================================


class TestValidateWorkflowDecision(unittest.TestCase):
    """validate_workflow_decision() — shape validation."""

    def test_valid_l1_decision(self):
        """🟢 L1 valid contract passes validation."""
        valid, errors = validate_workflow_decision(_make_valid_decision())
        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_not_a_dict(self):
        """Input is not a dict."""
        valid, errors = validate_workflow_decision("not a dict")
        self.assertFalse(valid)

    def test_missing_contract_version(self):
        """Missing contract_version."""
        d = _make_valid_decision()
        del d["contract_version"]
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("contract_version" in e for e in errors))

    def test_wrong_contract_version(self):
        """Wrong contract_version."""
        d = _make_valid_decision({"contract_version": "v2.0"})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("v3.3-wave1" in e for e in errors))

    def test_bad_request_id(self):
        """📏 request_id with wrong format."""
        d = _make_valid_decision({"request_id": "bad-id"})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("request_id" in e for e in errors))

    def test_unknown_final_layer(self):
        """📏 classifier_result.final_layer is unknown."""
        cr = dict(L1_CLASSIFIER)
        cr["final_layer"] = "L5_magic"
        d = _make_valid_decision({"classifier_result": cr})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("final_layer" in e for e in errors))

    def test_confidence_out_of_range_above(self):
        """📏 classifier_result.confidence > 1.0."""
        cr = dict(L1_CLASSIFIER)
        cr["confidence"] = 1.5
        d = _make_valid_decision({"classifier_result": cr})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("confidence" in e for e in errors))

    def test_confidence_out_of_range_below(self):
        """📏 classifier_result.confidence < 0.0."""
        cr = dict(L1_CLASSIFIER)
        cr["confidence"] = -0.1
        d = _make_valid_decision({"classifier_result": cr})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("confidence" in e for e in errors))

    def test_invalid_mode(self):
        """📏 classifier_result.mode is invalid."""
        cr = dict(L1_CLASSIFIER)
        cr["mode"] = "maybe"
        d = _make_valid_decision({"classifier_result": cr})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("mode" in e for e in errors))

    def test_unknown_lane(self):
        """📏 lane_decision.lane is unknown."""
        ld = dict(L1_LANE_DECISION)
        ld["lane"] = "L5_Unknown_Lane"
        d = _make_valid_decision({"lane_decision": ld})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("lane" in e for e in errors))

    def test_missing_governance(self):
        """Missing governance block."""
        d = _make_valid_decision()
        del d["governance"]
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("governance" in e for e in errors))

    def test_missing_classifier_result(self):
        """Missing classifier_result."""
        d = _make_valid_decision()
        del d["classifier_result"]
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("classifier_result" in e for e in errors))

    def test_missing_evidence(self):
        """Missing evidence block."""
        d = _make_valid_decision()
        del d["evidence"]
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("evidence" in e for e in errors))

    def test_execution_contract_missing_clarified_spec(self):
        """execution_contract missing clarified_spec."""
        ec = dict(L1_EXECUTION_CONTRACT)
        del ec["clarified_spec"]
        d = _make_valid_decision({"execution_contract": ec})
        valid, errors = validate_workflow_decision(d)
        self.assertFalse(valid)
        self.assertTrue(any("clarified_spec" in e for e in errors))


# ===========================================================================
# Test: governance_pre_dispatch
# ===========================================================================


class TestGovernancePreDispatch(unittest.TestCase):
    """governance_pre_dispatch() — safe-dispatch guard."""

    def test_l1_safe_to_dispatch(self):
        """🟢 L1 valid → safe to dispatch."""
        result, reason = governance_pre_dispatch(_make_valid_decision())
        self.assertTrue(result)
        self.assertIsNone(reason)

    def test_clarify_mode_blocked(self):
        """🔲 mode=clarify → blocked."""
        d = _make_valid_decision()
        d["classifier_result"]["mode"] = "clarify"
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIsNotNone(reason)
        self.assertIn("CLARIFY_MODE", reason)

    def test_final_layer_none_blocked(self):
        """🔲 final_layer=None → blocked."""
        d = _make_valid_decision()
        d["classifier_result"]["final_layer"] = None
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("UNKNOWN_LAYER", reason)

    def test_unknown_final_layer_blocked(self):
        """🔲 unknown final_layer → blocked."""
        d = _make_valid_decision()
        d["classifier_result"]["final_layer"] = "L9_unknown"
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("UNKNOWN_LAYER", reason)

    def test_unknown_lane_blocked(self):
        """📏 unknown lane → blocked."""
        d = _make_valid_decision()
        d["lane_decision"]["lane"] = "L5_Mystery"
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("UNKNOWN_LANE", reason)

    def test_safe_to_dispatch_false_blocked(self):
        """🔲 governance.safe_to_dispatch=False → blocked."""
        d = _make_valid_decision()
        d["governance"]["safe_to_dispatch"] = False
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("GOVERNANCE_BLOCKED", reason)

    def test_l4_valid_releaser(self):
        """🎯 L4 valid contract → safe to dispatch."""
        d = _make_valid_l4_decision()
        result, reason = governance_pre_dispatch(d)
        self.assertTrue(result)
        self.assertIsNone(reason)

    def test_l4_non_releaser_blocked(self):
        """🔴 L4 lane but target_agent is not Releaser → blocked."""
        d = _make_valid_l4_decision()
        d["adapter_dispatch"]["target_agent"] = "agent-developer"
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("ZERO_BYPASS_L4_GUARD", reason)

    def test_l4_missing_delegation_blocked(self):
        """🔴 L4 lane without l4_mandatory_delegation → blocked."""
        d = _make_valid_l4_decision()
        d["lane_decision"]["l4_mandatory_delegation"] = False
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("ZERO_BYPASS_L4_GUARD", reason)

    def test_l4_no_handoff_blocked(self):
        """🔴 L4 lane without required_handoff=agent-releaser → blocked."""
        d = _make_valid_l4_decision()
        d["governance"]["required_handoff"] = "agent-developer"
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("ZERO_BYPASS_L4_GUARD", reason)

    def test_l4_no_hitl_blocked(self):
        """🔴 L4 lane without hitl_required → blocked."""
        d = _make_valid_l4_decision()
        d["lane_decision"]["hitl_required"] = False
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("ZERO_BYPASS_L4_GUARD", reason)

    def test_lane_none_blocked(self):
        """lane_decision.lane is None → blocked."""
        d = _make_valid_decision()
        d["lane_decision"]["lane"] = None
        result, reason = governance_pre_dispatch(d)
        self.assertFalse(result)
        self.assertIn("UNKNOWN_LANE", reason)


# ===========================================================================
# Test: format_dispatch_payload
# ===========================================================================


class TestFormatDispatchPayload(unittest.TestCase):
    """format_dispatch_payload() — output shape and content."""

    def test_l1_payload_has_lane_and_bypass_risk(self):
        """🟢 L1 payload includes lane, required_agents, bypass_risk."""
        d = _make_valid_decision()
        payload = format_dispatch_payload(d)
        self.assertIn("metadata", payload)
        meta = payload["metadata"]
        self.assertIn("lane", meta)
        self.assertEqual(meta["lane"], "L1_Standard")
        self.assertIn("required_agents", meta)
        self.assertEqual(meta["required_agents"], ["Architect", "Developer", "QA"])
        self.assertIn("bypass_risk", meta)
        self.assertTrue(isinstance(meta["bypass_risk"], str) and len(meta["bypass_risk"]) > 0)

    def test_l2_payload_has_lane_and_bypass_risk(self):
        """🟢 L2 payload includes lane, required_agents, bypass_risk."""
        d = _make_valid_l2_decision()
        payload = format_dispatch_payload(d)
        meta = payload["metadata"]
        self.assertIn("lane", meta)
        self.assertEqual(meta["lane"], "L2_QuickFix")
        self.assertIn("required_agents", meta)
        self.assertIn("bypass_risk", meta)
        self.assertTrue(isinstance(meta["bypass_risk"], str) and len(meta["bypass_risk"]) > 0)

    def test_l4_releaser_payload_handoff(self):
        """🎯 L4 valid payload: target=agent-releaser, HITL=true, kind=handoff."""
        d = _make_valid_l4_decision()
        payload = format_dispatch_payload(d)
        self.assertEqual(payload["request_kind"], "handoff")
        self.assertEqual(payload["target_agent"], AGENT_RELEASER)
        self.assertEqual(payload["correlation_id"], "workflow-20260719-002")
        meta = payload["metadata"]
        self.assertTrue(meta["hitl_required"])
        self.assertEqual(meta["hitl_mode"], "pre_approval")
        self.assertTrue(meta["l4_mandatory_delegation"])

    def test_l0_fast_track_payload(self):
        """L0 Fast Track: kind=task, qa_required=False."""
        d = _make_valid_l0_decision()
        payload = format_dispatch_payload(d)
        self.assertEqual(payload["request_kind"], "task")
        meta = payload["metadata"]
        self.assertEqual(meta["lane"], "L0_Fast_Track")
        self.assertFalse(meta["qa_required"])
        self.assertFalse(meta["hitl_required"])
        self.assertIn("bypass_risk", meta)

    def test_l3_high_risk_payload(self):
        """L3 High Risk: kind=task, hitl_required=True, pre_approval."""
        d = _make_valid_l3_decision()
        payload = format_dispatch_payload(d)
        self.assertEqual(payload["request_kind"], "task")
        meta = payload["metadata"]
        self.assertEqual(meta["lane"], "L3_HighRisk")
        self.assertTrue(meta["hitl_required"])
        self.assertEqual(meta["hitl_mode"], "pre_approval")

    def test_payload_includes_task_summary(self):
        """Payload includes original_request as task_summary."""
        d = _make_valid_decision()
        payload = format_dispatch_payload(d)
        self.assertIn("task_summary", payload)
        self.assertEqual(payload["task_summary"], "Add new search provider")

    def test_payload_requires_reply_true(self):
        """requires_reply is always True."""
        d = _make_valid_decision()
        payload = format_dispatch_payload(d)
        self.assertTrue(payload["requires_reply"])

    def test_payload_includes_instructions(self):
        """Payload includes instructions with spec and success criteria."""
        d = _make_valid_decision()
        payload = format_dispatch_payload(d)
        self.assertIn("instructions", payload)
        self.assertIn("Add new search provider", payload["instructions"])
        self.assertIn("Tests pass", payload["instructions"])

    def test_correlation_id_from_adapter_dispatch(self):
        """correlation_id uses adapter_dispatch.correlation_id when present."""
        d = _make_valid_decision()
        payload = format_dispatch_payload(d)
        self.assertEqual(payload["correlation_id"], "workflow-20260719-001")

    def test_correlation_id_fallback_to_request_id(self):
        """correlation_id falls back to request_id."""
        d = _make_valid_decision()
        d["adapter_dispatch"]["correlation_id"] = ""
        payload = format_dispatch_payload(d)
        self.assertEqual(payload["correlation_id"], SAMPLE_REQUEST_ID)

    def test_forbidden_actions_in_instructions_l4(self):
        """L4 instructions include forbidden actions."""
        d = _make_valid_l4_decision()
        payload = format_dispatch_payload(d)
        self.assertIn("instructions", payload)
        self.assertIn("git merge", payload["instructions"])


# ===========================================================================
# Test: Integration — full pipeline
# ===========================================================================


class TestIntegrationPipeline(unittest.TestCase):
    """End-to-end: validate → governance → format for key scenarios."""

    def test_l4_valid_full_pipeline(self):
        """🎯 L4 valid contract through full pipeline → handoff to Releaser."""
        d = _make_valid_l4_decision()
        valid, errors = validate_workflow_decision(d)
        self.assertTrue(valid, f"validation errors: {errors}")
        safe, reason = governance_pre_dispatch(d)
        self.assertTrue(safe, f"governance blocked: {reason}")
        payload = format_dispatch_payload(d)
        self.assertEqual(payload["target_agent"], AGENT_RELEASER)
        self.assertEqual(payload["request_kind"], "handoff")
        self.assertTrue(payload["metadata"]["hitl_required"])
        self.assertTrue(payload["metadata"]["l4_mandatory_delegation"])

    def test_l1_valid_full_pipeline(self):
        """🟢 L1 valid through full pipeline → task to Architect."""
        d = _make_valid_decision()
        valid, errors = validate_workflow_decision(d)
        self.assertTrue(valid, f"validation errors: {errors}")
        safe, reason = governance_pre_dispatch(d)
        self.assertTrue(safe, f"governance blocked: {reason}")
        payload = format_dispatch_payload(d)
        self.assertEqual(payload["request_kind"], "task")
        self.assertEqual(payload["target_agent"], "Architect")

    def test_clarify_stops_before_dispatch(self):
        """🔲 clarify mode: validation passes, governance blocks, no dispatch."""
        d = _make_valid_decision()
        d["classifier_result"]["mode"] = "clarify"
        d["classifier_result"]["confidence"] = 0.0
        d["classifier_result"]["final_layer"] = None
        valid, errors = validate_workflow_decision(d)
        # validation should still pass (the mode "clarify" is valid, final_layer=None is OK for validate)
        self.assertTrue(valid, f"validation errors: {errors}")
        safe, reason = governance_pre_dispatch(d)
        self.assertFalse(safe)
        self.assertIn("CLARIFY_MODE", reason)

    def test_l4_non_releaser_stops_before_dispatch(self):
        """🔴 L4 non-Releaser: governance blocks, no payload."""
        d = _make_valid_l4_decision()
        d["adapter_dispatch"]["target_agent"] = "agent-developer"
        valid, errors = validate_workflow_decision(d)
        self.assertTrue(valid, f"validation errors: {errors}")
        safe, reason = governance_pre_dispatch(d)
        self.assertFalse(safe)
        self.assertIn("ZERO_BYPASS_L4_GUARD", reason)


# ===========================================================================
# Helpers: lane-specific valid decisions
# ===========================================================================


def _make_valid_l4_decision() -> dict:
    """Build a valid L4 decision contract with Releaser."""
    return {
        "contract_version": "v3.3-wave1",
        "request_id": "workflow-20260719-002",
        "created_at": "2026-07-19T00:00:00Z",
        "original_request": "release prod tag v1.2.3",
        "classifier_result": {
            "final_layer": "L4_release",
            "confidence": 0.95,
            "mode": "direct",
            "matched_keywords": {"L4_release": ["release", "prod", "tag"]},
            "top_score": 3,
            "second_score": 0,
            "dominance_applied": False,
            "next_step": "Delegate to agent-releaser",
            "l4_mandatory_delegation": True,
        },
        "execution_contract": {
            "clarified_spec": "Release version v1.2.3 to production",
            "scope_boundary": {
                "in_scope": ["release.json", "CHANGELOG.md", "tag v1.2.3"],
                "out_of_scope": ["Code changes", "New features"],
            },
            "success_criteria": [
                "Tag created",
                "Release notes generated",
                "CI/CD pipeline completes",
                "Healthcheck passes",
            ],
            "validation_plan": [
                "Verify tag format",
                "Check release.json consistency",
                "Confirm ancestry in main",
                "Human approval",
            ],
            "risk_level": "HIGH",
            "recommended_layer": "L4_release",
            "next_step": "Delegate to agent-releaser",
            "residual_ambiguity": [],
        },
        "lane_decision": {
            "lane": "L4_Releaser",
            "escalation_triggered": True,
            "escalation_reason": "L4 release task - mandatory delegation to agent-releaser",
            "required_agents": [AGENT_RELEASER],
            "qa_required": True,
            "hitl_required": True,
            "hitl_mode": "pre_approval",
            "l4_mandatory_delegation": True,
            "bypass_risk": "ZERO_BYPASS — mandatory agent-releaser delegation and HITL pre-approval",
        },
        "governance": {
            "forbidden_actions": [
                "git merge to mr/main",
                "git push origin mr/main",
                "git tag",
                "gh pr merge",
                "gh release create",
                "force-push",
            ],
            "required_handoff": AGENT_RELEASER,
            "human_approval_required": True,
            "safe_to_dispatch": True,
            "safe_stop_reason": None,
        },
        "adapter_dispatch": {
            "target_runtime": "native",
            "target_agent": AGENT_RELEASER,
            "correlation_id": "workflow-20260719-002",
            "dispatch_allowed": True,
            "dispatch_payload_kind": "handoff",
        },
        "evidence": {
            "source_files_read": ["scripts/intake_classify.py", "scripts/lane_select.py"],
            "validation_commands": [
                "python3 -m json.tool config/routing_map_v1.json",
                "python3 -m py_compile scripts/intake_classify.py",
            ],
            "event_refs": ["evt-20260719-002"],
            "audit_log_ref": None,
        },
    }


def _make_valid_l2_decision() -> dict:
    """Build a valid L2 QuickFix decision contract."""
    return {
        "contract_version": "v3.3-wave1",
        "request_id": "workflow-20260719-003",
        "created_at": "2026-07-19T00:00:00Z",
        "original_request": "Fix threshold bug in quality checks",
        "classifier_result": {
            "final_layer": "L2_bug_fix",
            "confidence": 0.87,
            "mode": "guarded",
            "matched_keywords": {"L2_bug_fix": ["fix", "bug", "threshold"]},
            "top_score": 3,
            "second_score": 1,
            "dominance_applied": False,
            "next_step": "Developer implements fix",
            "l4_mandatory_delegation": False,
        },
        "execution_contract": {
            "clarified_spec": "Fix false positive in threshold check",
            "scope_boundary": {
                "in_scope": ["src/quality/checks.py"],
                "out_of_scope": ["Search strategy changes", "New features"],
            },
            "success_criteria": [
                "Existing tests pass",
                "False positive rate < 5%",
                "No new false negatives",
            ],
            "validation_plan": ["Run existing test suite", "Manual verification"],
            "risk_level": "MEDIUM",
            "recommended_layer": "L2_bug_fix",
            "next_step": "Developer implements fix",
            "residual_ambiguity": [],
        },
        "lane_decision": {
            "lane": "L2_QuickFix",
            "escalation_triggered": False,
            "escalation_reason": None,
            "required_agents": ["Developer", "QA", "Architect (spot-check)"],
            "qa_required": True,
            "hitl_required": False,
            "hitl_mode": "review",
            "l4_mandatory_delegation": False,
            "bypass_risk": "MEDIUM — QA regression required",
        },
        "governance": {
            "forbidden_actions": [],
            "required_handoff": None,
            "human_approval_required": False,
            "safe_to_dispatch": True,
            "safe_stop_reason": None,
        },
        "adapter_dispatch": {
            "target_runtime": "native",
            "target_agent": "agent-developer",
            "correlation_id": "workflow-20260719-003",
            "dispatch_allowed": True,
            "dispatch_payload_kind": "task",
        },
        "evidence": {
            "source_files_read": ["src/quality/checks.py"],
            "validation_commands": ["python3 -m pytest tests/"],
            "event_refs": ["evt-20260719-003"],
            "audit_log_ref": None,
        },
    }


def _make_valid_l0_decision() -> dict:
    """Build a valid L0 Fast Track decision contract."""
    return {
        "contract_version": "v3.3-wave1",
        "request_id": "workflow-20260719-004",
        "created_at": "2026-07-19T00:00:00Z",
        "original_request": "Update ruff.toml to add new lint rule",
        "classifier_result": {
            "final_layer": "L0_config_housekeeping",
            "confidence": 0.92,
            "mode": "direct",
            "matched_keywords": {"L0_config_housekeeping": ["update", "config", "ruff"]},
            "top_score": 3,
            "second_score": 0,
            "dominance_applied": False,
            "next_step": "Update ruff.toml",
            "l4_mandatory_delegation": False,
        },
        "execution_contract": {
            "clarified_spec": "Update ruff.toml to add new lint rule",
            "scope_boundary": {
                "in_scope": ["ruff.toml"],
                "out_of_scope": ["src/", "tests/", "serverless.yml", "release.json"],
            },
            "success_criteria": ["Ruff passes with new rule"],
            "validation_plan": ["Run ruff check"],
            "risk_level": "LOW",
            "recommended_layer": "L0_config_housekeeping",
            "next_step": "Update ruff.toml",
            "residual_ambiguity": [],
        },
        "lane_decision": {
            "lane": "L0_Fast_Track",
            "escalation_triggered": False,
            "escalation_reason": None,
            "required_agents": ["Developer"],
            "qa_required": False,
            "hitl_required": False,
            "hitl_mode": "auto_approve",
            "l4_mandatory_delegation": False,
            "bypass_risk": "LOW — guarded by E1-E7; no QA only if eligible",
        },
        "governance": {
            "forbidden_actions": [],
            "required_handoff": None,
            "human_approval_required": False,
            "safe_to_dispatch": True,
            "safe_stop_reason": None,
        },
        "adapter_dispatch": {
            "target_runtime": "native",
            "target_agent": "agent-developer",
            "correlation_id": "workflow-20260719-004",
            "dispatch_allowed": True,
            "dispatch_payload_kind": "task",
        },
        "evidence": {
            "source_files_read": ["ruff.toml"],
            "validation_commands": ["ruff check"],
            "event_refs": ["evt-20260719-004"],
            "audit_log_ref": None,
        },
    }


def _make_valid_l3_decision() -> dict:
    """Build a valid L3 High Risk decision contract."""
    return {
        "contract_version": "v3.3-wave1",
        "request_id": "workflow-20260719-005",
        "created_at": "2026-07-19T00:00:00Z",
        "original_request": "Extract abstract base class for processors",
        "classifier_result": {
            "final_layer": "L3_refactor",
            "confidence": 0.91,
            "mode": "guarded",
            "matched_keywords": {"L3_refactor": ["refactor", "extract", "base", "class"]},
            "top_score": 4,
            "second_score": 1,
            "dominance_applied": False,
            "next_step": "Architect planning",
            "l4_mandatory_delegation": False,
        },
        "execution_contract": {
            "clarified_spec": "Extract abstract base class for processors",
            "scope_boundary": {
                "in_scope": ["src/processors/*.py"],
                "out_of_scope": ["API changes", "Database schema"],
            },
            "success_criteria": [
                "Abstract base created",
                "All processors inherit correctly",
                "Tests pass",
            ],
            "validation_plan": [
                "Full test suite",
                "Architecture review",
                "Pre-approval required",
            ],
            "risk_level": "HIGH",
            "recommended_layer": "L3_refactor",
            "next_step": "Architect planning",
            "residual_ambiguity": [],
        },
        "lane_decision": {
            "lane": "L3_HighRisk",
            "escalation_triggered": True,
            "escalation_reason": "L3 refactoring - HIGH risk requires pre-approval",
            "required_agents": ["Architect", "Developer", "QA"],
            "qa_required": True,
            "hitl_required": True,
            "hitl_mode": "pre_approval",
            "l4_mandatory_delegation": False,
            "bypass_risk": "HIGH — pre-approval required; no bypass allowed",
        },
        "governance": {
            "forbidden_actions": [],
            "required_handoff": None,
            "human_approval_required": True,
            "safe_to_dispatch": True,
            "safe_stop_reason": None,
        },
        "adapter_dispatch": {
            "target_runtime": "native",
            "target_agent": "agent-developer",
            "correlation_id": "workflow-20260719-005",
            "dispatch_allowed": True,
            "dispatch_payload_kind": "task",
        },
        "evidence": {
            "source_files_read": ["src/processors/*.py"],
            "validation_commands": ["python3 -m pytest tests/"],
            "event_refs": ["evt-20260719-005"],
            "audit_log_ref": None,
        },
    }


# ===========================================================================
# Test: WorkflowEventWriter and event emission
# ===========================================================================


class TestWorkflowEventWriter(unittest.TestCase):
    """WorkflowEventWriter — initialization, event emission, privacy."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_event_writer_init(self):
        """🟢 Writer creates its events directory."""
        writer = WorkflowEventWriter(events_dir=self.tmp)
        self.assertTrue(Path(self.tmp).exists())
        self.assertEqual(writer.events_dir, Path(self.tmp))
        self.assertEqual(writer._default_privacy_mode, "summary")

    def test_emit_event_classified(self):
        """🟢 workflow.classified event has correct envelope and data fields."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            eid = writer.emit_event(
                "workflow.classified",
                {
                    "final_layer": "L2_bug_fix",
                    "confidence": 0.87,
                    "mode": "guarded",
                    "matched_layers": ["L2_bug_fix", "L1_feature_dev"],
                    "dominance_applied": False,
                    "original_request_summary": "fix threshold bug in quality checks module",
                },
                workflow_id="workflow-20260720-001",
                original_request="fix threshold bug in quality checks module",
            )
        self.assertTrue(eid.startswith("evt-"))
        self.assertEqual(len(eid.split("-")), 4)  # evt-<sessionid>-<seq> (session contains no hyphens)
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        evt = json.loads(lines[0])
        self.assertEqual(evt["event_type"], "workflow.classified")
        self.assertEqual(evt["workflow_id"], "workflow-20260720-001")
        self.assertEqual(evt["schema_version"], "v3.7-b1")
        self.assertIn("timestamp", evt)
        self.assertIn("source", evt)
        self.assertEqual(evt["data"]["final_layer"], "L2_bug_fix")
        self.assertEqual(evt["data"]["confidence"], 0.87)
        self.assertEqual(evt["privacy"]["mode"], "summary")

    def test_emit_event_lane_selected(self):
        """🟢 workflow.lane_selected event has correct lane and agent data."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            writer.emit_event(
                "workflow.lane_selected",
                {
                    "lane": "L2_QuickFix",
                    "required_agents": ["Developer", "QA", "Architect (spot-check)"],
                    "qa_required": True,
                    "hitl_required": False,
                    "hitl_mode": "review",
                    "bypass_risk": "MEDIUM — QA regression required",
                    "l4_mandatory_delegation": False,
                    "escalation_triggered": False,
                    "escalation_reason": None,
                },
                workflow_id="workflow-20260720-002",
            )
        lines = self._read_lines()
        evt = json.loads(lines[0])
        self.assertEqual(evt["event_type"], "workflow.lane_selected")
        self.assertEqual(evt["data"]["lane"], "L2_QuickFix")
        self.assertTrue(evt["data"]["qa_required"])
        self.assertFalse(evt["data"]["l4_mandatory_delegation"])

    def test_emit_event_governance_blocked(self):
        """🟢 workflow.governance_blocked event has blocking reason and rule."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            writer.emit_event(
                "workflow.governance_blocked",
                {
                    "reason": "CLARIFY_MODE — request requires clarification before dispatch",
                    "blocking_rule": "mode_clarify",
                    "final_layer": None,
                    "confidence": 0.0,
                    "mode": "clarify",
                    "lane": None,
                },
                workflow_id="workflow-20260720-003",
            )
        lines = self._read_lines()
        evt = json.loads(lines[0])
        self.assertEqual(evt["event_type"], "workflow.governance_blocked")
        self.assertIn("reason", evt["data"])
        self.assertIn("blocking_rule", evt["data"])
        self.assertIn("CLARIFY_MODE", evt["data"]["reason"])

    def test_emit_event_dispatched(self):
        """🟢 workflow.dispatched event has correct dispatch fields."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            writer.emit_event(
                "workflow.dispatched",
                {
                    "request_kind": "task",
                    "target_agent": "agent-developer",
                    "correlation_id": "workflow-20260720-001",
                    "payload_kind": "task",
                    "lane": "L2_QuickFix",
                    "requires_reply": True,
                },
                workflow_id="workflow-20260720-001",
            )
        lines = self._read_lines()
        evt = json.loads(lines[0])
        self.assertEqual(evt["event_type"], "workflow.dispatched")
        self.assertEqual(evt["data"]["request_kind"], "task")
        self.assertEqual(evt["data"]["target_agent"], "agent-developer")

    def test_emit_event_validate_result(self):
        """🟢 workflow.validate_result event has item_id, result, attempt, max_retry."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            writer.emit_event(
                "workflow.validate_result",
                {
                    "item_id": "pool-20260720-001",
                    "result": "PASS",
                    "attempt": 1,
                    "max_retry": 3,
                    "layer": "L2_bug_fix",
                    "lane": "L2_QuickFix",
                    "validator": "agent-qa",
                    "duration_seconds": 12.34,
                    "details_summary": "All 15 tests passed",
                },
                workflow_id="workflow-20260720-001",
            )
        lines = self._read_lines()
        evt = json.loads(lines[0])
        self.assertEqual(evt["event_type"], "workflow.validate_result")
        self.assertEqual(evt["data"]["item_id"], "pool-20260720-001")
        self.assertEqual(evt["data"]["result"], "PASS")
        self.assertEqual(evt["data"]["attempt"], 1)

    def test_event_id_unique(self):
        """🟢 Sequential IDs within a session are monotonically increasing."""
        ids: list[str] = []
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            for i in range(5):
                eid = writer.emit_event(
                    "workflow.classified",
                    {"final_layer": "L1_feature_dev", "confidence": 0.8, "mode": "guarded",
                     "matched_layers": [], "dominance_applied": False, "original_request_summary": "test"},
                    original_request="test",
                )
                ids.append(eid)
        self.assertEqual(len(ids), len(set(ids)), "event_ids must be unique")
        # Check monotonic seq numbers
        for i, eid in enumerate(ids, start=1):
            self.assertTrue(eid.endswith(f"{i:05d}"))

    def test_event_jsonl_valid(self):
        """🟢 Every emitted line is valid JSON parseable by json.loads."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            for i, et in enumerate(EVENT_TYPES):
                writer.emit_event(et, {"test": i})
        lines = self._read_lines()
        self.assertEqual(len(lines), len(EVENT_TYPES))
        for line in lines:
            try:
                json.loads(line)
            except json.JSONDecodeError:
                self.fail(f"Line is not valid JSON: {line!r}")

    def test_sink_failure_no_crash(self):
        """🔴 Write failure does not crash caller; event_id is still returned."""
        # Use /dev/full or a non-writable path simulation
        writer = WorkflowEventWriter(events_dir="/dev/null")
        eid = writer.emit_event(
            "workflow.classified",
            {"final_layer": "L1", "confidence": 0.8, "mode": "direct",
             "matched_layers": [], "dominance_applied": False, "original_request_summary": "x"},
        )
        self.assertTrue(eid.startswith("evt-"))
        # Should not raise
        writer.close()

    def test_privacy_default_summary(self):
        """🟢 Default summary mode truncates request to 80 chars."""
        long_request = "a" * 200
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            writer.emit_event(
                "workflow.classified",
                {"final_layer": "L1", "confidence": 0.8, "mode": "direct",
                 "matched_layers": [], "dominance_applied": False, "original_request_summary": long_request},
                original_request=long_request,
            )
        lines = self._read_lines()
        evt = json.loads(lines[0])
        self.assertEqual(evt["privacy"]["mode"], "summary")
        self.assertTrue(evt["privacy"]["original_request_redacted"])
        self.assertEqual(len(evt["privacy"]["original_request_preview"]), 83)  # 80 + "..."
        self.assertEqual(evt["privacy"]["original_request_preview"][-3:], "...")

    def test_privacy_raw_mode(self):
        """🟢 Raw mode preserves full original_request without truncation."""
        long_request = "a" * 200
        with WorkflowEventWriter(events_dir=self.tmp, privacy_mode="raw") as writer:
            writer.emit_event(
                "workflow.classified",
                {"final_layer": "L1", "confidence": 0.8, "mode": "direct",
                 "matched_layers": [], "dominance_applied": False, "original_request_summary": long_request},
                original_request=long_request,
                privacy_mode="raw",
            )
        lines = self._read_lines()
        evt = json.loads(lines[0])
        self.assertEqual(evt["privacy"]["mode"], "raw")
        self.assertFalse(evt["privacy"]["original_request_redacted"])
        self.assertEqual(evt["privacy"]["original_request_preview"], long_request)

    def test_validate_event_line_valid(self):
        """🟢 validate_event_line returns (True, []) for a valid event."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            writer.emit_event(
                "workflow.classified",
                {"final_layer": "L1", "confidence": 0.8, "mode": "direct",
                 "matched_layers": [], "dominance_applied": False, "original_request_summary": "test"},
                original_request="test",
            )
        lines = self._read_lines()
        valid, errs = validate_event_line(lines[0])
        self.assertTrue(valid, f"Expected valid, got errors: {errs}")

    def test_validate_event_line_invalid(self):
        """🔴 validate_event_line returns errors for bad event."""
        bad = '{"event_id": "bad-id", "event_type": "workflow.unknown"}'
        valid, errs = validate_event_line(bad)
        self.assertFalse(valid)
        self.assertTrue(len(errs) > 0)

    def test_validate_event_file(self):
        """🟢 validate_event_file returns counts and error list."""
        with WorkflowEventWriter(events_dir=self.tmp) as writer:
            writer.emit_event(
                "workflow.classified",
                {"final_layer": "L1", "confidence": 0.8, "mode": "direct",
                 "matched_layers": [], "dominance_applied": False, "original_request_summary": "test"},
                original_request="test",
            )
            writer.emit_event(
                "workflow.lane_selected",
                {"lane": "L1_Standard", "required_agents": [], "qa_required": True,
                 "hitl_required": False, "hitl_mode": "review", "bypass_risk": "LOW",
                 "l4_mandatory_delegation": False, "escalation_triggered": False, "escalation_reason": None},
            )
        result = validate_event_file(Path(self.tmp) / f"events-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl")
        self.assertEqual(result["valid_lines"], 2)
        self.assertEqual(result["invalid_lines"], 0)
        self.assertEqual(result["total_lines"], 2)
        self.assertIn("workflow.classified", result["event_type_counts"])

    # ------------------------------------------------------------------
    # Internal helpers for tests
    # ------------------------------------------------------------------

    def _read_lines(self) -> list[str]:
        """Return all lines from today's event file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fpath = Path(self.tmp) / f"events-{today}.jsonl"
        if not fpath.exists():
            return []
        with open(fpath, "r", encoding="utf-8") as fh:
            return [line.rstrip("\n") for line in fh]


# ===========================================================================
# main
# ===========================================================================

if __name__ == "__main__":
    unittest.main()
