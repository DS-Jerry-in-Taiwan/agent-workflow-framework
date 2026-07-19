#!/usr/bin/env python3
"""
Unit tests for scripts/orchestrator.py — v3.5 Workstream A.

Covers:
- find_active_pool_item() with various pool states
- build_active_plan_from_item() field mapping and defaults
- build_continuation_context_from_item() field extraction
- evaluate_message_against_item() full continuation flow
- should_dispatch_continuation() gate logic
- Backward compatibility with old pool items lacking continuation_policy
"""

import copy
import json
import unittest
from pathlib import Path

from scripts.orchestrator import (
    find_active_pool_item,
    build_active_plan_from_item,
    build_continuation_context_from_item,
    evaluate_message_against_item,
    should_dispatch_continuation,
)
from scripts.continuation_policy import (
    AUTO_CONTINUE,
    REPORT_ONLY,
    ASK_USER,
    MANDATORY_HANDOFF,
    BLOCKED,
)


# =============================================================================
# Sample pool items (with and without continuation_policy)
# =============================================================================

SAMPLE_ITEM_WITH_CP = {
    "id": "pool-20260719-001",
    "title": "Fix threshold bug",
    "status": "in_progress",
    "execution_contract": {
        "next_step": "Run QA validation",
        "risk_level": "MEDIUM",
        "recommended_layer": "L2_bug_fix",
    },
    "lane_decision": {
        "lane": "L2_QuickFix",
        "l4_mandatory_delegation": False,
        "qa_required": True,
        "hitl_required": False,
        "hitl_mode": "review",
    },
    "retry_count": 0,
    "max_retry": 3,
    "continuation_policy": {
        "auto_continue_allowed": True,
        "checkpoint_complete": False,
        "current_phase": "qa",
        "last_decision": None,
        "context_flags": {},
    },
}

SAMPLE_ITEM_WITHOUT_CP = {
    "id": "pool-old-001",
    "title": "Old task without continuation_policy",
    "status": "in_progress",
    "execution_contract": {
        "next_step": "Pending review",
    },
    "lane_decision": {
        "lane": "L1_Standard",
        "l4_mandatory_delegation": False,
    },
    "retry_count": 0,
    "max_retry": 3,
}

SAMPLE_ITEM_L4 = {
    "id": "pool-l4-001",
    "title": "Prod release",
    "status": "in_progress",
    "execution_contract": {
        "next_step": "Delegate to agent-releaser",
        "risk_level": "HIGH",
        "recommended_layer": "L4_release",
    },
    "lane_decision": {
        "lane": "L4_Releaser",
        "l4_mandatory_delegation": True,
        "qa_required": True,
        "hitl_required": True,
        "hitl_mode": "pre_approval",
    },
    "retry_count": 0,
    "max_retry": 3,
    "continuation_policy": {
        "auto_continue_allowed": True,
        "checkpoint_complete": False,
        "current_phase": "release",
        "last_decision": None,
        "context_flags": {},
    },
}


# =============================================================================
# Tests — find_active_pool_item
# =============================================================================


class TestFindActivePoolItem(unittest.TestCase):
    """find_active_pool_item() locates the active item from the pool index."""

    def test_returns_active_item(self):
        """🟢 Pool with active item returns the matching index entry."""
        pool_index = {
            "items": [
                {"id": "pool-001", "status": "in_progress", "title": "Task A"},
            ]
        }
        result = find_active_pool_item(pool_index)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "pool-001")

    def test_returns_first_active_when_multiple(self):
        """Returns the first entry matching active status."""
        pool_index = {
            "items": [
                {"id": "pool-001", "status": "completed"},
                {"id": "pool-002", "status": "in_progress", "title": "Active"},
                {"id": "pool-003", "status": "picked", "title": "Also active"},
            ]
        }
        result = find_active_pool_item(pool_index)
        self.assertEqual(result["id"], "pool-002")

    def test_picked_is_active(self):
        """picked status is considered active."""
        pool_index = {
            "items": [
                {"id": "pool-001", "status": "picked"},
            ]
        }
        result = find_active_pool_item(pool_index)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "pool-001")

    def test_qa_pending_is_active(self):
        """qa_pending status is considered active."""
        pool_index = {
            "items": [
                {"id": "pool-001", "status": "qa_pending"},
            ]
        }
        result = find_active_pool_item(pool_index)
        self.assertIsNotNone(result)

    def test_no_active_items_returns_none(self):
        """🔴 Pool with only terminal items returns None."""
        pool_index = {
            "items": [
                {"id": "pool-001", "status": "completed"},
                {"id": "pool-002", "status": "cancelled"},
            ]
        }
        result = find_active_pool_item(pool_index)
        self.assertIsNone(result)

    def test_empty_pool_returns_none(self):
        """📏 Empty pool returns None."""
        result = find_active_pool_item({"items": []})
        self.assertIsNone(result)

    def test_items_missing_status_skipped(self):
        """🔲 Items missing status field are skipped safely."""
        pool_index = {
            "items": [
                {"id": "pool-001"},
                {"id": "pool-002", "status": "in_progress"},
            ]
        }
        result = find_active_pool_item(pool_index)
        self.assertEqual(result["id"], "pool-002")


# =============================================================================
# Tests — build_active_plan_from_item
# =============================================================================


class TestBuildActivePlanFromItem(unittest.TestCase):
    """build_active_plan_from_item() maps pool item fields to active_plan."""

    def test_full_item_with_continuation_policy(self):
        """🟢 Item with continuation_policy maps correctly."""
        plan = build_active_plan_from_item(SAMPLE_ITEM_WITH_CP)
        self.assertEqual(plan["id"], "pool-20260719-001")
        self.assertEqual(plan["next_planned_step"], "Run QA validation")
        self.assertEqual(plan["auto_continue_allowed"], True)
        self.assertEqual(plan["checkpoint_complete"], False)
        self.assertEqual(plan["current_phase"], "qa")
        self.assertEqual(plan["retry_count"], 0)
        self.assertEqual(plan["max_retry"], 3)

    def test_old_item_without_continuation_policy(self):
        """🔲 Old item without continuation_policy uses safe defaults."""
        plan = build_active_plan_from_item(SAMPLE_ITEM_WITHOUT_CP)
        self.assertEqual(plan["id"], "pool-old-001")
        self.assertEqual(plan["next_planned_step"], "Pending review")
        # Safe defaults
        self.assertEqual(plan["auto_continue_allowed"], False)
        self.assertEqual(plan["checkpoint_complete"], False)
        # current_phase falls back to status
        self.assertEqual(plan["current_phase"], "in_progress")
        self.assertEqual(plan["retry_count"], 0)
        self.assertEqual(plan["max_retry"], 3)

    def test_empty_item_uses_defaults(self):
        """Empty dict produces safe defaults."""
        plan = build_active_plan_from_item({})
        self.assertIsNone(plan["id"])
        self.assertIsNone(plan["next_planned_step"])
        self.assertEqual(plan["auto_continue_allowed"], False)
        self.assertEqual(plan["checkpoint_complete"], False)
        self.assertEqual(plan["retry_count"], 0)
        self.assertEqual(plan["max_retry"], 3)

    def test_next_step_mapping_from_execution_contract(self):
        """next_planned_step maps from execution_contract.next_step."""
        item = {"execution_contract": {"next_step": "Deploy to staging"}}
        plan = build_active_plan_from_item(item)
        self.assertEqual(plan["next_planned_step"], "Deploy to staging")

    def test_execution_contract_missing_next_step(self):
        """Missing next_step produces None in active_plan."""
        item = {"execution_contract": {}}
        plan = build_active_plan_from_item(item)
        self.assertIsNone(plan["next_planned_step"])


# =============================================================================
# Tests — build_continuation_context_from_item
# =============================================================================


class TestBuildContinuationContextFromItem(unittest.TestCase):
    """build_continuation_context_from_item() extracts context fields."""

    def test_l2_item_without_context_flags(self):
        """🟢 L2 item produces correct context with defaults."""
        ctx = build_continuation_context_from_item(SAMPLE_ITEM_WITH_CP)
        self.assertEqual(ctx["layer"], "L2_QuickFix")
        self.assertEqual(ctx["is_l4_operation"], False)
        self.assertEqual(ctx["mandatory_delegation"], False)
        self.assertEqual(ctx["force_push"], False)
        self.assertEqual(ctx["secrets_involved"], False)

    def test_l4_item_produces_l4_context(self):
        """🎯 L4 item sets is_l4_operation=True and mandatory_delegation=True."""
        ctx = build_continuation_context_from_item(SAMPLE_ITEM_L4)
        self.assertEqual(ctx["layer"], "L4_Releaser")
        self.assertEqual(ctx["is_l4_operation"], True)
        self.assertEqual(ctx["mandatory_delegation"], True)

    def test_old_item_without_continuation_policy(self):
        """🔲 Old item without continuation_policy uses safe defaults."""
        ctx = build_continuation_context_from_item(SAMPLE_ITEM_WITHOUT_CP)
        self.assertEqual(ctx["layer"], "L1_Standard")
        self.assertEqual(ctx["is_l4_operation"], False)
        self.assertEqual(ctx["force_push"], False)
        self.assertEqual(ctx["secrets_involved"], False)

    def test_context_flags_are_propagated(self):
        """📏 Context flags from continuation_policy.context_flags flow through."""
        item = {
            "lane_decision": {"lane": "L2_Investigate", "l4_mandatory_delegation": False},
            "continuation_policy": {
                "context_flags": {
                    "force_push": True,
                    "secrets_involved": True,
                    "classifier_semantic_change": True,
                    "uncovered_runtime_change": True,
                    "ambiguous_failure": True,
                }
            },
        }
        ctx = build_continuation_context_from_item(item)
        self.assertTrue(ctx["force_push"])
        self.assertTrue(ctx["secrets_involved"])
        self.assertTrue(ctx["classifier_semantic_change"])
        self.assertTrue(ctx["uncovered_runtime_change"])
        self.assertTrue(ctx["ambiguous_failure"])

    def test_empty_item_returns_safe_defaults(self):
        """Empty item yields all False / empty defaults."""
        ctx = build_continuation_context_from_item({})
        self.assertEqual(ctx["layer"], "")
        self.assertEqual(ctx["is_l4_operation"], False)
        self.assertEqual(ctx["force_push"], False)


# =============================================================================
# Tests — evaluate_message_against_item
# =============================================================================


class TestEvaluateMessageAgainstItem(unittest.TestCase):
    """evaluate_message_against_item() orchestrates full continuation flow."""

    def test_auto_continue_with_signal(self):
        """🟢 Continuation signal + auto_continue_allowed + next_step → auto_continue."""
        result = evaluate_message_against_item("continue", SAMPLE_ITEM_WITH_CP)
        self.assertEqual(result["state"], AUTO_CONTINUE)
        self.assertEqual(result["next_action"], "Run QA validation")
        self.assertIsNotNone(result["matched_continuation_signal"])

    def test_ask_user_when_not_allowed(self):
        """🔴 Continuation signal without auto_continue_allowed → ask_user."""
        item_not_allowed = copy.deepcopy(SAMPLE_ITEM_WITH_CP)
        item_not_allowed["continuation_policy"] = {
            "auto_continue_allowed": False,
        }
        result = evaluate_message_against_item("continue", item_not_allowed)
        self.assertEqual(result["state"], ASK_USER)
        self.assertIsNone(result["next_action"])

    def test_no_active_item_returns_ask_user(self):
        """🔲 No active item (None) → ask_user with safe default."""
        result = evaluate_message_against_item("continue", None)
        self.assertEqual(result["state"], ASK_USER)
        self.assertIn("No active", result["reason"])

    def test_l4_item_protected_operation(self):
        """🎯 Message with release + L4 context → mandatory_handoff."""
        result = evaluate_message_against_item("release prod", SAMPLE_ITEM_L4)
        self.assertEqual(result["state"], MANDATORY_HANDOFF)
        self.assertIn("releaser", result["reason"].lower())

    def test_force_push_blocked(self):
        """Blocked on force push."""
        item = copy.deepcopy(SAMPLE_ITEM_WITH_CP)
        item["continuation_policy"] = {
            "auto_continue_allowed": True,
            "context_flags": {"force_push": True},
        }
        result = evaluate_message_against_item("push to main", item)
        self.assertEqual(result["state"], BLOCKED)

    def test_retry_exhausted(self):
        """📏 retry_count >= max_retry → ask_user with retry_exhausted."""
        item = copy.deepcopy(SAMPLE_ITEM_WITH_CP)
        item["retry_count"] = 3
        item["max_retry"] = 3
        result = evaluate_message_against_item("continue", item)
        self.assertEqual(result["state"], ASK_USER)
        self.assertIn("retry_exhausted", result["must_stop_triggers"])

    def test_retry_not_exhausted(self):
        """📏 retry_count=2, max_retry=3 → still auto-continue."""
        item = copy.deepcopy(SAMPLE_ITEM_WITH_CP)
        item["retry_count"] = 2
        result = evaluate_message_against_item("continue", item)
        self.assertEqual(result["state"], AUTO_CONTINUE)

    def test_old_item_without_continuation_policy(self):
        """🔲 Old item without continuation_policy — safe defaults."""
        result = evaluate_message_against_item("fix this", SAMPLE_ITEM_WITHOUT_CP)
        # No continuation signal, auto_continue_allowed default False → ask_user
        self.assertEqual(result["state"], ASK_USER)
        self.assertIsNone(result["next_action"])

    def test_report_only_checkpoint_complete(self):
        """Checkpoint complete with no next_step → report_only."""
        item = copy.deepcopy(SAMPLE_ITEM_WITH_CP)
        item["continuation_policy"] = {
            "auto_continue_allowed": True,
            "checkpoint_complete": True,
        }
        item["execution_contract"]["next_step"] = None
        result = evaluate_message_against_item("continue", item)
        self.assertEqual(result["state"], REPORT_ONLY)


# =============================================================================
# Tests — should_dispatch_continuation
# =============================================================================


class TestShouldDispatchContinuation(unittest.TestCase):
    """should_dispatch_continuation() gates safe dispatch."""

    def test_auto_continue_dispatch_allowed(self):
        """🟢 auto_continue may dispatch."""
        self.assertTrue(should_dispatch_continuation({"state": AUTO_CONTINUE}))

    def test_mandatory_handoff_dispatch_allowed(self):
        """mandatory_handoff may dispatch."""
        self.assertTrue(should_dispatch_continuation({"state": MANDATORY_HANDOFF}))

    def test_ask_user_no_dispatch(self):
        """🔴 ask_user must NOT dispatch."""
        self.assertFalse(should_dispatch_continuation({"state": ASK_USER}))

    def test_report_only_no_dispatch(self):
        """report_only must NOT dispatch."""
        self.assertFalse(should_dispatch_continuation({"state": REPORT_ONLY}))

    def test_blocked_no_dispatch(self):
        """blocked must NOT dispatch."""
        self.assertFalse(should_dispatch_continuation({"state": BLOCKED}))

    def test_unknown_state_no_dispatch(self):
        """🔲 Unknown state defaults to no dispatch."""
        self.assertFalse(should_dispatch_continuation({"state": "unknown"}))
        self.assertFalse(should_dispatch_continuation({}))

    def test_none_state_no_dispatch(self):
        """State=None defaults to no dispatch."""
        self.assertFalse(should_dispatch_continuation({"state": None}))


# =============================================================================
# Tests — Backward compatibility / edge cases
# =============================================================================


class TestBackwardCompatibility(unittest.TestCase):
    """Existing pool artifacts without continuation_policy must not crash."""

    def test_load_item_without_cp_then_evaluate(self):
        """Old item loaded from file (no CP) → evaluate without crash."""
        result = evaluate_message_against_item("continue", SAMPLE_ITEM_WITHOUT_CP)
        self.assertIsInstance(result, dict)
        self.assertIn("state", result)

    def test_build_plan_from_partial_item(self):
        """Partial old item (missing fields) → safe defaults."""
        minimal_item = {"id": "minimal"}
        plan = build_active_plan_from_item(minimal_item)
        self.assertEqual(plan["auto_continue_allowed"], False)
        self.assertEqual(plan["retry_count"], 0)
        self.assertIsNone(plan["next_planned_step"])

    def test_build_context_from_partial_item(self):
        """Partial old item yields safe defaults for context."""
        minimal_item = {}
        ctx = build_continuation_context_from_item(minimal_item)
        self.assertEqual(ctx["is_l4_operation"], False)
        self.assertFalse(ctx["secrets_involved"])

    def test_find_active_skips_items_without_status(self):
        """Items in pool index without 'status' field are skipped."""
        pool_index = {
            "items": [
                {"id": "missing-status"},
                {"id": "active-one", "status": "in_progress"},
            ]
        }
        result = find_active_pool_item(pool_index)
        self.assertEqual(result["id"], "active-one")


# =============================================================================
# Tests — Result structure contract
# =============================================================================


class TestResultStructureContract(unittest.TestCase):
    """Returned dict from evaluate_message_against_item has required keys."""

    REQUIRED_RESULT_KEYS = [
        "state", "reason", "next_action",
        "human_required_reason", "must_stop_triggers",
        "matched_continuation_signal",
    ]

    def test_result_has_all_required_keys_with_item(self):
        """Result from evaluate_message_against_item with item."""
        result = evaluate_message_against_item("continue", SAMPLE_ITEM_WITH_CP)
        for key in self.REQUIRED_RESULT_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, result)

    def test_result_has_all_required_keys_without_item(self):
        """Result from evaluate_message_against_item without item."""
        result = evaluate_message_against_item("fix bug", None)
        for key in self.REQUIRED_RESULT_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, result)

    def test_state_is_valid_string(self):
        """State is a non-empty string."""
        result = evaluate_message_against_item("continue", SAMPLE_ITEM_WITH_CP)
        self.assertIsInstance(result["state"], str)
        self.assertTrue(len(result["state"]) > 0)

    def test_must_stop_triggers_is_list(self):
        """must_stop_triggers is always a list."""
        result = evaluate_message_against_item("continue", SAMPLE_ITEM_WITH_CP)
        self.assertIsInstance(result["must_stop_triggers"], list)

    def test_next_action_is_string_or_none(self):
        """next_action is a string or None."""
        result = evaluate_message_against_item("continue", SAMPLE_ITEM_WITH_CP)
        self.assertTrue(
            result["next_action"] is None or isinstance(result["next_action"], str)
        )


if __name__ == "__main__":
    unittest.main()
