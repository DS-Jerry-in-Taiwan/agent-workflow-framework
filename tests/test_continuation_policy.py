#!/usr/bin/env python3
"""
Test Suite for Autonomous Continuation Policy — v3.4 Phase C Runtime MVP

Tests cover:
- auto_continue: continuation signals with active plan
- report_only: checkpoint complete with no next step
- ask_user: no active plan, retry exhausted, scope expansion
- mandatory_handoff: L4/protected operations
- blocked: force push
- Parallel work package evaluation
"""

import json
import unittest
from scripts.continuation_policy import (
    decide_continuation,
    evaluate_parallel_work_packages,
    is_continuation_signal,
    is_new_task,
    AUTO_CONTINUE,
    REPORT_ONLY,
    ASK_USER,
    MANDATORY_HANDOFF,
    BLOCKED,
    MUST_STOP_TRIGGERS,
    APPROVAL_SIGNALS,
    CONTINUATION_PROMPTS,
    SHARED_CANONICAL_FILES,
)


class TestContinuationSignalDetection(unittest.TestCase):
    """Test continuation signal detection (approval signals and continuation prompts)."""

    def test_approval_signals_positive(self):
        """Positive cases: short approval signals should return True."""
        positive_signals = [
            "同意", "yes", "ok", "okay", "continue", "go ahead", "proceed",
            "y", "sure", "fine", "do it", "lets go", "go for it",
        ]
        for signal in positive_signals:
            with self.subTest(signal=signal):
                self.assertTrue(
                    is_continuation_signal(signal),
                    f"Expected '{signal}' to be detected as continuation signal"
                )

    def test_continuation_prompts_positive(self):
        """Positive cases: continuation prompts should return True."""
        positive_prompts = [
            "接下來", "繼續", "下一步", "next step", "then what",
            "what next", "continue if you have next steps",
            "keep going", "carry on",
        ]
        for prompt in positive_prompts:
            with self.subTest(prompt=prompt):
                self.assertTrue(
                    is_continuation_signal(prompt),
                    f"Expected '{prompt}' to be detected as continuation signal"
                )

    def test_approval_signals_startswith_path(self):
        """Approval signals with trailing text should match via startswith path."""
        startswith_cases = [
            "yes let's do it",
            "ok sounds good",
            "continue please",
            "go ahead with the next step",
            "sure thing",
            "fine by me",
            "do it now",
            "y let's proceed",
        ]
        for msg in startswith_cases:
            with self.subTest(msg=msg):
                self.assertTrue(
                    is_continuation_signal(msg),
                    f"Expected '{msg}' to match via startswith path"
                )

    def test_next_word_boundary_positive(self):
        """Word-boundary 'next' should match for continuation intent."""
        positive_next = [
            "what next",
            "next please",
            "show me next",
        ]
        for msg in positive_next:
            with self.subTest(msg=msg):
                self.assertTrue(
                    is_continuation_signal(msg),
                    f"Expected '{msg}' to match 'next' via word boundary"
                )

    def test_next_word_boundary_negative(self):
        """Temporal 'next week/month/year' should NOT match as continuation."""
        negative_next = [
            "next week",
            "next month",
            "next year",
            "schedule for next time",
        ]
        for msg in negative_next:
            with self.subTest(msg=msg):
                self.assertFalse(
                    is_continuation_signal(msg),
                    f"Expected '{msg}' NOT to match continuation (temporal 'next')"
                )

    def test_continuation_signal_negative(self):
        """Negative cases: non-continuation messages should return False."""
        negative_messages = [
            "fix the bug in src/quality/checks.py",
            "add new feature",
            "release v1.2.3 to production",
            "deploy to prod",
            "merge to main",
        ]
        for msg in negative_messages:
            with self.subTest(msg=msg):
                self.assertFalse(
                    is_continuation_signal(msg),
                    f"Expected '{msg}' NOT to be detected as continuation signal"
                )

    def test_is_new_task_positive(self):
        """Positive cases: new task messages should return True."""
        new_task_messages = [
            "add new feature",
            "create new API endpoint",
            "implement new search strategy",
            "release v1.2.3",
            "deploy to prod",
            "push to origin main",
            "merge to main",
        ]
        for msg in new_task_messages:
            with self.subTest(msg=msg):
                self.assertTrue(
                    is_new_task(msg),
                    f"Expected '{msg}' to be detected as new task"
                )

    def test_is_new_task_negative(self):
        """Negative cases: continuation messages should return False."""
        continuation_messages = [
            "同意", "yes", "continue", "接下來", "下一步",
            "fix threshold bug",
        ]
        for msg in continuation_messages:
            with self.subTest(msg=msg):
                self.assertFalse(
                    is_new_task(msg),
                    f"Expected '{msg}' NOT to be detected as new task"
                )


class TestAutoContinue(unittest.TestCase):
    """Test auto_continue state decisions."""

    def test_continuation_with_active_plan(self):
        """同意 + active plan → auto_continue."""
        active_plan = {
            "id": "plan-001",
            "next_planned_step": "Create design doc",
            "auto_continue_allowed": True,
        }
        result = decide_continuation("同意", active_plan)
        self.assertEqual(result["state"], AUTO_CONTINUE)
        self.assertIn("next step", result["reason"].lower())

    def test_continue_with_active_plan_next_step(self):
        """接下來要做什麼 + active plan + next step → auto_continue."""
        active_plan = {
            "id": "plan-001",
            "current_phase": "planning",
            "next_planned_step": "Create design doc",
            "auto_continue_allowed": True,
        }
        result = decide_continuation("接下來要做什麼", active_plan)
        self.assertEqual(result["state"], AUTO_CONTINUE)
        self.assertEqual(result["next_action"], "Create design doc")

    def test_yes_with_active_plan(self):
        """yes + active plan → auto_continue."""
        active_plan = {
            "id": "plan-002",
            "next_planned_step": "Run validation",
            "auto_continue_allowed": True,
        }
        result = decide_continuation("yes", active_plan)
        self.assertEqual(result["state"], AUTO_CONTINUE)

    def test_continue_with_next_step_no_explicit_approval(self):
        """continue but auto_continue_allowed not set → ask_user."""
        active_plan = {
            "id": "plan-003",
            "next_planned_step": "Run tests",
            "auto_continue_allowed": False,
        }
        result = decide_continuation("continue", active_plan)
        self.assertEqual(result["state"], ASK_USER)
        self.assertIn("not explicitly allowed", result["reason"].lower())

    def test_continue_with_checkpoint_complete(self):
        """Checkpoint complete with no next step → report_only, not auto_continue."""
        active_plan = {
            "id": "plan-004",
            "checkpoint_complete": True,
            "next_planned_step": None,
        }
        result = decide_continuation("continue", active_plan)
        self.assertEqual(result["state"], REPORT_ONLY)


class TestReportOnly(unittest.TestCase):
    """Test report_only state decisions."""

    def test_checkpoint_complete_no_next_step(self):
        """Report checkpoint with no next step → report_only."""
        active_plan = {
            "id": "plan-005",
            "checkpoint_complete": True,
            "next_planned_step": None,
        }
        result = decide_continuation("同意", active_plan)
        self.assertEqual(result["state"], REPORT_ONLY)
        self.assertIsNone(result["next_action"])

    def test_checkpoint_complete_with_next_step(self):
        """Checkpoint complete but next step exists → report_only (stop at checkpoint)."""
        active_plan = {
            "id": "plan-006",
            "checkpoint_complete": True,
            "next_planned_step": "Run QA validation",
        }
        result = decide_continuation("ok", active_plan)
        self.assertEqual(result["state"], REPORT_ONLY)

    def test_no_next_step_defined(self):
        """No next step defined in plan → report_only."""
        active_plan = {
            "id": "plan-007",
            "next_planned_step": None,
        }
        result = decide_continuation("continue", active_plan)
        self.assertEqual(result["state"], REPORT_ONLY)


class TestAskUser(unittest.TestCase):
    """Test ask_user state decisions."""

    def test_no_active_plan(self):
        """No active plan → ask_user."""
        result = decide_continuation("fix the bug")
        self.assertEqual(result["state"], ASK_USER)
        self.assertIn("No active plan", result["human_required_reason"])

    def test_retry_exhausted(self):
        """retry_count == max_retry → ask_user with retry_exhausted trigger."""
        active_plan = {
            "id": "plan-008",
            "retry_count": 3,
            "max_retry": 3,
            "next_planned_step": "Retry fix",
        }
        result = decide_continuation("continue", active_plan)
        self.assertEqual(result["state"], ASK_USER)
        self.assertIn("retry_exhausted", result["must_stop_triggers"])
        self.assertIn("exhausted", result["human_required_reason"].lower())

    def test_retry_exhausted_with_retry_count_3(self):
        """retry_count = 3, max_retry = 3 → ask_user."""
        active_plan = {
            "id": "plan-009",
            "retry_count": 3,
            "max_retry": 3,
        }
        result = decide_continuation("ok", active_plan)
        self.assertEqual(result["state"], ASK_USER)

    def test_retry_below_max(self):
        """retry_count < max_retry → NOT ask_user (should be auto_continue or report_only)."""
        active_plan = {
            "id": "plan-010",
            "retry_count": 1,
            "max_retry": 3,
            "next_planned_step": "Retry fix",
            "auto_continue_allowed": True,
        }
        result = decide_continuation("continue", active_plan)
        # Should NOT be ask_user with retry_exhausted
        self.assertNotEqual(result["state"], ASK_USER)
        self.assertNotIn("retry_exhausted", result["must_stop_triggers"])

    def test_scope_expansion(self):
        """Scope expansion detected → ask_user."""
        active_plan = {
            "id": "plan-011",
            "next_planned_step": "Fix config",
        }
        context = {
            "new_scope_keywords": ["new feature", "new API"],
        }
        result = decide_continuation("add new feature", active_plan, context)
        self.assertEqual(result["state"], ASK_USER)
        self.assertIn("scope_expansion", result["must_stop_triggers"])

    def test_uncovered_runtime_change(self):
        """Uncovered runtime change → ask_user."""
        active_plan = {
            "id": "plan-012",
        }
        context = {
            "uncovered_runtime_change": True,
        }
        result = decide_continuation("fix this", active_plan, context)
        self.assertEqual(result["state"], ASK_USER)
        self.assertIn("uncovered_runtime_change", result["must_stop_triggers"])


class TestMandatoryHandoff(unittest.TestCase):
    """Test mandatory_handoff state decisions for L4/protected operations."""

    def test_release_prod(self):
        """release prod → mandatory_handoff."""
        active_plan = {
            "id": "plan-013",
        }
        result = decide_continuation("release prod", active_plan)
        self.assertEqual(result["state"], MANDATORY_HANDOFF)
        self.assertIn("releaser", result["human_required_reason"].lower())

    def test_deploy_production(self):
        """deploy production → mandatory_handoff."""
        result = decide_continuation("deploy production")
        self.assertEqual(result["state"], MANDATORY_HANDOFF)

    def test_tag_version(self):
        """tag v1.2.3 → mandatory_handoff."""
        result = decide_continuation("tag v1.2.3")
        self.assertEqual(result["state"], MANDATORY_HANDOFF)

    def test_merge_main(self):
        """merge to main → mandatory_handoff."""
        result = decide_continuation("merge to main")
        self.assertEqual(result["state"], MANDATORY_HANDOFF)
        self.assertIn("branch_promotion_protected", result["must_stop_triggers"])

    def test_push_origin_main(self):
        """push to origin main → mandatory_handoff."""
        result = decide_continuation("push to origin main")
        self.assertEqual(result["state"], MANDATORY_HANDOFF)

    def test_context_l4_layer(self):
        """Context with layer=L4_release → mandatory_handoff."""
        context = {"layer": "L4_release"}
        result = decide_continuation("fix config", context=context)
        self.assertEqual(result["state"], MANDATORY_HANDOFF)

    def test_context_mandatory_delegation(self):
        """Context with mandatory_delegation=True → mandatory_handoff."""
        context = {"mandatory_delegation": True}
        result = decide_continuation("deploy", context=context)
        self.assertEqual(result["state"], MANDATORY_HANDOFF)


class TestBlocked(unittest.TestCase):
    """Test blocked state for force push."""

    def test_force_push(self):
        """force push → blocked."""
        result = decide_continuation("force push to main")
        self.assertEqual(result["state"], BLOCKED)
        self.assertIn("force_push", result["must_stop_triggers"])

    def test_force_push_withLease(self):
        """force push with-lease → blocked."""
        result = decide_continuation("git push --force-with-lease origin main")
        self.assertEqual(result["state"], BLOCKED)

    def test_context_force_push(self):
        """Context with force_push indicator → blocked."""
        context = {"force_push": True}
        result = decide_continuation("push", context=context)
        self.assertEqual(result["state"], BLOCKED)


class TestResultStructure(unittest.TestCase):
    """Test that result structure meets contract."""

    def test_result_has_required_keys(self):
        """Result dict must have required keys."""
        active_plan = {"id": "plan-014", "next_planned_step": "test"}
        result = decide_continuation("同意", active_plan)

        required_keys = [
            "state", "reason", "next_action",
            "human_required_reason", "must_stop_triggers",
            "matched_continuation_signal"
        ]
        for key in required_keys:
            with self.subTest(key=key):
                self.assertIn(key, result, f"Missing required key: {key}")

    def test_state_is_valid(self):
        """State must be one of valid states."""
        valid_states = [AUTO_CONTINUE, REPORT_ONLY, ASK_USER, MANDATORY_HANDOFF, BLOCKED]
        # Test a few cases
        test_cases = [
            ("release prod", None, None),
            ("同意", {"id": "p1", "next_planned_step": "test", "auto_continue_allowed": True}, None),
        ]
        for msg, plan, ctx in test_cases:
            result = decide_continuation(msg, plan, ctx)
            with self.subTest(msg=msg):
                self.assertIn(result["state"], valid_states)


class TestMustStopTriggers(unittest.TestCase):
    """Test must-stop triggers vocabulary."""

    def test_must_stop_triggers_defined(self):
        """MUST_STOP_TRIGGERS must contain all required triggers."""
        required_triggers = [
            "release_deploy_tag_prod",
            "branch_promotion_protected",
            "force_push",
            "scope_expansion",
            "uncovered_runtime_change",
            "classifier_semantic_change",
            "ambiguous_repair_path",
            "retry_exhausted",
            "secrets_credentials",
        ]
        for trigger in required_triggers:
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, MUST_STOP_TRIGGERS)


class TestParallelWorkPackages(unittest.TestCase):
    """Test parallel work package evaluation."""

    def test_empty_packages(self):
        """Empty package list → not allowed."""
        result = evaluate_parallel_work_packages([])
        self.assertFalse(result["allowed"])
        self.assertIn("No packages", result["reasons"][0])
        self.assertIn("warnings", result)

    def test_single_package_allowed(self):
        """Single package with validation → allowed."""
        pkg = {
            "id": "pkg-001",
            "affected_files": ["docs/README.md"],
            "validation_command": "python3 -m pytest tests/",
        }
        result = evaluate_parallel_work_packages([pkg])
        self.assertTrue(result["allowed"])
        self.assertEqual(len(result["parallel_packages"]), 1)
        self.assertIn("warnings", result)

    def test_single_l4_package_blocked(self):
        """Single L4 package → not allowed."""
        pkg = {
            "id": "pkg-l4",
            "affected_files": ["release.json"],
            "is_l4_operation": True,
        }
        result = evaluate_parallel_work_packages([pkg])
        self.assertFalse(result["allowed"])
        self.assertEqual(len(result["blocked_packages"]), 1)

    def test_single_package_missing_validation(self):
        """Package missing validation command → not allowed."""
        pkg = {
            "id": "pkg-002",
            "affected_files": ["src/test.py"],
        }
        result = evaluate_parallel_work_packages([pkg])
        self.assertFalse(result["allowed"])
        self.assertIn("validation", result["blocked_packages"][0]["reasons"][0].lower())

    def test_parallel_docs_packages_separate_worktrees(self):
        """Parallel docs packages in separate worktrees → allowed."""
        pkg1 = {
            "id": "pkg-docs-1",
            "worktree": "wt-planning",
            "affected_files": ["docs/planning/design.md"],
            "validation_command": "python3 -m markdown docs/planning/design.md",
        }
        pkg2 = {
            "id": "pkg-docs-2",
            "worktree": "wt-policy",
            "affected_files": ["docs/policy/rules.md"],
            "validation_command": "python3 -m markdown docs/policy/rules.md",
        }
        result = evaluate_parallel_work_packages([pkg1, pkg2])
        self.assertTrue(result["allowed"])
        self.assertEqual(len(result["parallel_packages"]), 2)

    def test_parallel_packages_touching_lane_select_blocked(self):
        """Packages touching lane_select.py → not allowed without serial ordering."""
        pkg1 = {
            "id": "pkg-mod-1",
            "affected_files": ["scripts/lane_select.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        pkg2 = {
            "id": "pkg-mod-2",
            "affected_files": ["scripts/lane_select.py"],
            "validation_command": "python3 -m py_compile scripts/lane_select.py",
        }
        result = evaluate_parallel_work_packages([pkg1, pkg2])
        self.assertFalse(result["allowed"])
        self.assertIn("lane_select.py", result["reasons"][0])

    def test_parallel_packages_touching_intake_classify_blocked(self):
        """Packages touching intake_classify.py → not allowed."""
        pkg1 = {
            "id": "pkg-cls-1",
            "affected_files": ["scripts/intake_classify.py"],
            "validation_command": "python3 scripts/intake_classify.py test",
        }
        pkg2 = {
            "id": "pkg-cls-2",
            "affected_files": ["docs/README.md"],
            "validation_command": "cat docs/README.md",
        }
        result = evaluate_parallel_work_packages([pkg1, pkg2])
        self.assertFalse(result["allowed"])

    def test_parallel_packages_disjoint_files(self):
        """Packages touching disjoint files → allowed."""
        pkg1 = {
            "id": "pkg-a",
            "affected_files": ["src/a.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        pkg2 = {
            "id": "pkg-b",
            "affected_files": ["src/b.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        result = evaluate_parallel_work_packages([pkg1, pkg2])
        self.assertTrue(result["allowed"])

    def test_parallel_packages_l4_blocked(self):
        """L4 operation in parallel packages → blocked."""
        pkg1 = {
            "id": "pkg-release",
            "affected_files": ["release.json"],
            "is_l4_operation": True,
        }
        pkg2 = {
            "id": "pkg-docs",
            "affected_files": ["docs/a.md"],
            "validation_command": "cat docs/a.md",
        }
        result = evaluate_parallel_work_packages([pkg1, pkg2])
        self.assertFalse(result["allowed"])
        self.assertIn("L4", result["blocked_packages"][0]["reasons"][0])

    # ------------------------------------------------------------------
    # Recommendation 1: Non-canonical file conflict detection
    # ------------------------------------------------------------------

    def test_non_canonical_conflict_warning_safe_mode(self):
        """Same non-canonical file in multiple packages → warning in safe mode."""
        pkg1 = {
            "id": "pkg-src-a",
            "affected_files": ["src/shared.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        pkg2 = {
            "id": "pkg-src-b",
            "affected_files": ["src/shared.py", "src/other.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        result = evaluate_parallel_work_packages([pkg1, pkg2])
        # Still allowed (warning only) in safe mode
        self.assertTrue(result["allowed"])
        self.assertEqual(len(result["warnings"]), 1)
        warning = result["warnings"][0]
        self.assertIn("Non-canonical file conflict", warning)
        self.assertIn("src/shared.py", warning)
        self.assertIn("Safe mode", warning)

    def test_non_canonical_conflict_block_strict_mode(self):
        """Same non-canonical file in multiple packages → blocked in strict mode."""
        pkg1 = {
            "id": "pkg-strict-a",
            "affected_files": ["src/shared.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        pkg2 = {
            "id": "pkg-strict-b",
            "affected_files": ["src/shared.py", "src/other.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        result = evaluate_parallel_work_packages(
            [pkg1, pkg2], strict_non_canonical_conflicts=True
        )
        self.assertFalse(result["allowed"])
        self.assertIn("Non-canonical file conflict", result["reasons"][0])

    def test_non_canonical_conflict_canonical_only_safe(self):
        """Only canonical files are shared — warnings, not block, unless conflicting."""
        pkg1 = {
            "id": "pkg-can-a",
            "affected_files": ["scripts/intake_classify.py"],
            "validation_command": "python3 scripts/intake_classify.py test",
        }
        pkg2 = {
            "id": "pkg-can-b",
            "affected_files": ["scripts/lane_select.py"],
            "validation_command": "python3 scripts/lane_select.py --sample L0_Fast_Track",
        }
        # Different canonical files — no conflict, each pkg blocked individually
        result = evaluate_parallel_work_packages([pkg1, pkg2])
        self.assertFalse(result["allowed"])
        self.assertEqual(len(result["blocked_packages"]), 2)
        # No non-canonical warnings
        non_canonical_warnings = [w for w in result.get("warnings", [])
                                  if "Non-canonical file conflict" in w]
        self.assertEqual(len(non_canonical_warnings), 0)

    # ------------------------------------------------------------------
    # Recommendation 7: Single package touching canonical files
    # ------------------------------------------------------------------

    def test_single_package_canonical_file_warning(self):
        """Single package touching canonical file → warning, not blocked."""
        pkg = {
            "id": "pkg-canon-single",
            "affected_files": ["scripts/intake_classify.py"],
            "validation_command": "python3 scripts/intake_classify.py test",
        }
        result = evaluate_parallel_work_packages([pkg])
        self.assertTrue(result["allowed"])  # single pkg is always allowed
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("canonical file", result["warnings"][0].lower())

    def test_single_package_non_canonical_no_warning(self):
        """Single package touching non-canonical files → no canonical-file warning."""
        pkg = {
            "id": "pkg-safe-single",
            "affected_files": ["src/safe.py"],
            "validation_command": "python3 -m pytest tests/",
        }
        result = evaluate_parallel_work_packages([pkg])
        self.assertTrue(result["allowed"])
        self.assertEqual(len(result["warnings"]), 0)


class TestReturnValueTypes(unittest.TestCase):
    """Test return value types for API compatibility."""

    def test_decide_continuation_returns_dict(self):
        """decide_continuation must return a dict."""
        result = decide_continuation("test")
        self.assertIsInstance(result, dict)

    def test_evaluate_parallel_returns_dict(self):
        """evaluate_parallel_work_packages must return a dict."""
        result = evaluate_parallel_work_packages([])
        self.assertIsInstance(result, dict)

    def test_must_stop_triggers_is_list(self):
        """MUST_STOP_TRIGGERS must be a list."""
        self.assertIsInstance(MUST_STOP_TRIGGERS, list)

    def test_approval_signals_contains_expected(self):
        """APPROVAL_SIGNALS must contain expected signals."""
        expected = ["同意", "yes", "ok", "continue", "go ahead"]
        for signal in expected:
            self.assertIn(signal, APPROVAL_SIGNALS)

    def test_shared_canonical_files_contains_prohibited(self):
        """SHARED_CANONICAL_FILES must include protected files."""
        expected = [
            "scripts/intake_classify.py",
            "scripts/lane_select.py",
            "docs/intake_layer/routing_map_v1.json",
        ]
        for file_path in expected:
            self.assertIn(file_path, SHARED_CANONICAL_FILES)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_empty_message(self):
        """Empty message → ask_user."""
        result = decide_continuation("")
        self.assertEqual(result["state"], ASK_USER)

    def test_whitespace_message(self):
        """Whitespace-only message → ask_user."""
        result = decide_continuation("   ")
        self.assertEqual(result["state"], ASK_USER)

    def test_case_insensitive_l4_detection(self):
        """L4 detection should be case-insensitive."""
        messages = [
            "RELEASE PROD",
            "Deploy Production",
            "TAG V1.2.3",
            "PUSH TO ORIGIN MAIN",
        ]
        for msg in messages:
            with self.subTest(msg=msg):
                result = decide_continuation(msg)
                self.assertEqual(
                    result["state"],
                    MANDATORY_HANDOFF,
                    f"Expected mandatory_handoff for '{msg}'"
                )

    def test_active_plan_with_retry_at_2_of_3(self):
        """retry_count=2, max_retry=3 → can still auto-continue."""
        active_plan = {
            "id": "plan-015",
            "retry_count": 2,
            "max_retry": 3,
            "next_planned_step": "Retry fix",
            "auto_continue_allowed": True,
        }
        result = decide_continuation("continue", active_plan)
        # Should NOT be retry_exhausted
        self.assertNotIn("retry_exhausted", result["must_stop_triggers"])

    def test_context_with_multiple_triggers(self):
        """Multiple triggers in context → all should be captured."""
        context = {
            "secrets_involved": True,
            "classifier_semantic_change": True,
        }
        result = decide_continuation("fix this", context=context)
        self.assertIn("secrets_credentials", result["must_stop_triggers"])
        self.assertIn("classifier_semantic_change", result["must_stop_triggers"])


if __name__ == "__main__":
    unittest.main()
