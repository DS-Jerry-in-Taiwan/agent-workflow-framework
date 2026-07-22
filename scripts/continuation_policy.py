#!/usr/bin/env python3
"""
Autonomous Continuation Policy Evaluator — v3.4 Phase C Runtime MVP

Deterministic policy for deciding whether an agent should:
- auto_continue: Continue to next planned step without asking
- report_only: Send checkpoint summary, no approval requested
- ask_user: Stop and ask for decision
- mandatory_handoff: Delegate to required role (e.g., Releaser)
- blocked: Cannot proceed safely

This module is standalone and does NOT modify:
- scripts/intake_classify.py
- scripts/lane_select.py
- scripts/pool.py
- scripts/observability_report.py
- config/routing_map_v1.json

Usage (library):
    from scripts.continuation_policy import decide_continuation, evaluate_parallel_work_packages

Usage (CLI):
    python3 scripts/continuation_policy.py --message "同意" --active-plan-id "plan-001"
    python3 scripts/continuation_policy.py --message "接下來要做什麼" --has-active-plan --next-step "Create design doc"
    python3 scripts/continuation_policy.py --message "release prod" --active-plan-id "plan-001"
"""

import argparse
import json
import re
import sys
from typing import Optional

# =============================================================================
# Policy State Constants
# =============================================================================

AUTO_CONTINUE = "auto_continue"
REPORT_ONLY = "report_only"
ASK_USER = "ask_user"
MANDATORY_HANDOFF = "mandatory_handoff"
BLOCKED = "blocked"

VALID_STATES = [AUTO_CONTINUE, REPORT_ONLY, ASK_USER, MANDATORY_HANDOFF, BLOCKED]

# =============================================================================
# Must-Stop Triggers (Controlled Vocabulary)
# =============================================================================

MUST_STOP_TRIGGERS = [
    "release_deploy_tag_prod",       # release/deploy/tag/prod/production
    "branch_promotion_protected",    # PR merge, branch promotion to mr/main
    "force_push",                    # force push or force-with-lease
    "scope_expansion",               # scope expansion beyond approved plan
    "uncovered_runtime_change",       # runtime code change not covered by DeveloperPrompt
    "classifier_semantic_change",     # classifier/lane/routing map semantic change
    "ambiguous_repair_path",          # QA/Expert FAIL with multiple repair options
    "retry_exhausted",               # retry_count >= max_retry
    "secrets_credentials",           # secrets/config credentials involved
]

# =============================================================================
# Protected Operation Patterns (L4 / Releaser)
# =============================================================================

L4_PROTECTED_PATTERNS = [
    # Release/deploy/tag keywords
    r"\brelease[sd]?\b",
    r"\bdeploy[sd]?\b",
    r"\btag\s+(?:v?\d|prod|production|main)",
    r"\bprod(uction)?\b",
    r"\bproduction\b",
    r"\bv\d+\.\d+\.\d+\b",  # version tags like v1.2.3
    # Push/merge/branch promotion
    r"\bpush(?:es|ed|ing)?\s+(?:to\s+)?(?:origin\s+)?(?:main|mr|master|prod)",
    r"\bmerge[sd]?\b",
    r"\bbranch\s+promotion\b",
    r"\bpromote[sd]?\b",
    r"\bforce[- ]push(?:es|ed|ing)?\b",
    r"\bforce[- ]with[- ]lease\b",
    # CI/CD deployment
    r"\bworkflow[- ]dispatch\b",
    r"\bdeploy[- ]prod\b",
    r"\bdeploy[- ]dev\b",
]

# Compile patterns for efficiency
L4_PROTECTED_RE = re.compile("|".join(L4_PROTECTED_PATTERNS), re.IGNORECASE)


# =============================================================================
# Continuation Signal Detection
# =============================================================================

# Short approval signals
APPROVAL_SIGNALS = [
    "同意", "yes", "ok", "okay", "continue", "go ahead", "proceed",
    "y", "sure", "fine", "do it", "lets go", "let's go", "go for it",
]

# Next-step / continuation prompts.
# NOTE: "next" is matched word-boundary (\bnext\b) to avoid false positives
# like "next week" or "next time".  Other multi-word prompts use substring
# containment because they are specific enough not to cause broad false matches.
CONTINUATION_PROMPTS = [
    "接下來", "繼續", "下一步",
    "then what", "what next",
    "continue if you have next steps", "keep going", "carry on",
    "do the next step", "proceed with next", "go on",
]


def is_continuation_signal(message: str) -> bool:
    """
    Detect if message is a continuation signal (short approval or next-step prompt).
    Returns True if message matches continuation signals, False otherwise.

    Word-boundary handling:
      - "next" is matched with ``\\bnext\\b`` to avoid false positives
        such as "next week" or "next time".
      - Multi-word prompts use substring containment (specific enough).
    """
    normalized = message.lower().strip()

    # Check exact matches
    for signal in APPROVAL_SIGNALS:
        if normalized == signal or normalized.startswith(signal + " "):
            return True

    # Check continuation prompts
    for prompt in CONTINUATION_PROMPTS:
        if prompt.lower() in normalized:
            return True

    # Word-boundary check for bare "next" (avoids "next week" false positive).
    # "next step" is still valid (handled by "do the next step" / "proceed with next" above).
    if re.search(r"\bnext\b", normalized):
        # Exclude temporal phrases like "next week/month/year"
        if not re.search(r"\bnext\s+(week|month|year|time)\b", normalized):
            return True

    return False


def is_new_task(message: str) -> bool:
    """
    Detect if message is a new task rather than a continuation.
    These anti-signals force standalone classification or clarification.
    """
    normalized = message.lower().strip()

    # New scope indicators
    new_scope_patterns = [
        r"\bnew\s+(?:feature|processor|service|api|component)\b",
        r"\badd\s+(?:new|another)\b",
        r"\bcreate\s+(?:new|a\s+new)\b",
        r"\bbuild\s+(?:new|a)\b",
        r"\bimplement\s+(?:new|a)\b",
    ]

    # Release/deploy/push/merge patterns (anti-signals for continuation)
    protected_patterns = [
        r"\brelease\b",
        r"\bdeploy\b",
        r"\btag\b",
        r"\bprod\b",
        r"\bproduction\b",
        r"\bpush\s+(?:to\s+)?(?:origin\s+)?(?:main|mr|master)\b",
        r"\bmerge\s+(?:to\s+)?(?:main|mr|master)\b",
        r"\bbranch\s+promotion\b",
        r"\bforce[- ]push\b",
    ]

    all_patterns = new_scope_patterns + protected_patterns

    for pattern in all_patterns:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True

    return False


# =============================================================================
# Core Decision Logic
# =============================================================================

def check_l4_protected(message: str, context: Optional[dict] = None) -> tuple[bool, list[str]]:
    """
    Check if message or context contains L4 protected operations.
    Returns (is_protected, matched_triggers).
    """
    matched = []
    normalized = message.lower()

    # Check message text
    if L4_PROTECTED_RE.search(message):
        matched.append("release_deploy_tag_prod")
        # Refine: check for push/merge specifically
        if re.search(r"\bpush\b", normalized):
            matched.append("branch_promotion_protected")
        if re.search(r"\bmerge\b", normalized):
            matched.append("branch_promotion_protected")
        if re.search(r"\bforce", normalized):
            matched.append("force_push")

    # Check context for L4 indicators
    if context:
        if context.get("is_l4_operation"):
            matched.append("release_deploy_tag_prod")
        if context.get("layer") == "L4_release":
            matched.append("release_deploy_tag_prod")
        if context.get("mandatory_delegation"):
            matched.append("release_deploy_tag_prod")
        if context.get("force_push"):
            matched.append("force_push")

    # Deduplicate
    matched = list(dict.fromkeys(matched))
    return bool(matched), matched


def check_must_stop_triggers(
    message: str,
    context: Optional[dict] = None,
) -> tuple[bool, list[str]]:
    """
    Check all must-stop conditions.
    Returns (has_trigger, matched_triggers).

    .. note::
       **Secrets detection limitation**: Currently relies entirely on
       ``context.secrets_involved`` being set by the caller.  There is no
       built-in secret scanning of the message text.  If the caller does
       not populate ``context["secrets_involved"]``, secrets-related
       operations will not be flagged.

    .. note::
       **Scope expansion is NOT checked here**; it is handled upstream in
       ``decide_continuation`` (which calls this function *after* resolving
       scope expansion so the early-return prevents double-processing).
    """
    matched_triggers = []

    # 1. L4 Protected operations
    is_protected, l4_triggers = check_l4_protected(message, context)
    matched_triggers.extend(l4_triggers)

    # 2. Uncovered runtime change
    if context and context.get("uncovered_runtime_change"):
        matched_triggers.append("uncovered_runtime_change")

    # 4. Classifier semantic change
    if context and context.get("classifier_semantic_change"):
        matched_triggers.append("classifier_semantic_change")

    # 5. Secrets/credentials
    if context and context.get("secrets_involved"):
        matched_triggers.append("secrets_credentials")

    # Deduplicate
    matched_triggers = list(dict.fromkeys(matched_triggers))
    return bool(matched_triggers), matched_triggers


def check_retry_exhausted(retry_count: int, max_retry: int) -> bool:
    """Check if retry budget is exhausted."""
    return retry_count >= max_retry


# =============================================================================
# Main Decision Function
# =============================================================================

def decide_continuation(
    message: str,
    active_plan: Optional[dict] = None,
    context: Optional[dict] = None
) -> dict:
    """
    Main continuation decision function.

    Args:
        message: User message to evaluate
        active_plan: Active execution plan dict with optional fields:
            - id: str
            - current_phase: str
            - next_planned_step: str
            - auto_continue_allowed: bool
            - checkpoint_complete: bool
            - retry_count: int
            - max_retry: int
        context: Additional context dict with optional fields:
            - layer: str (L0-L4)
            - is_l4_operation: bool
            - mandatory_delegation: bool
            - new_scope_keywords: list[str]
            - uncovered_runtime_change: bool
            - classifier_semantic_change: bool
            - secrets_involved: bool
            - ambiguous_failure: bool

    Returns:
        dict with keys:
            - state: auto_continue|report_only|ask_user|mandatory_handoff|blocked
            - reason: str
            - next_action: str|null
            - human_required_reason: str|null
            - must_stop_triggers: list[str]
            - matched_continuation_signal: str|null

    .. note::
       **Secrets detection limitation**: Secrets/credentials detection
       depends on the caller populating ``context["secrets_involved"]``.
       No message-text scanning for secrets is performed by this function.
    """
    # Initialize
    state = ASK_USER  # Default to ask_user
    reason = "Default: no active plan"
    next_action = None
    human_required_reason = None
    must_stop_triggers = []
    matched_continuation_signal = None

    # Extract retry info
    retry_count = 0
    max_retry = 3
    if active_plan:
        retry_count = active_plan.get("retry_count", 0)
        max_retry = active_plan.get("max_retry", 3)

    # =========================================================================
    # Rule 1: Protected/L4 operations → mandatory_handoff or blocked
    # =========================================================================
    is_protected, l4_triggers = check_l4_protected(message, context)
    if is_protected:
        if "force_push" in l4_triggers:
            state = BLOCKED
            reason = "Force push is blocked without explicit Releaser governance approval"
            human_required_reason = "Force push requires Releaser governance"
        else:
            state = MANDATORY_HANDOFF
            reason = "L4/release/protected operation detected - must delegate to agent-releaser"
            human_required_reason = "Release/deploy/tag/merge operations must be delegated to agent-releaser"

        must_stop_triggers.extend(l4_triggers)
        return _build_result(
            state, reason, next_action, human_required_reason,
            must_stop_triggers, matched_continuation_signal
        )

    # =========================================================================
    # Rule 2: Retry exhausted → ask_user
    # =========================================================================
    if check_retry_exhausted(retry_count, max_retry):
        state = ASK_USER
        reason = f"Retry count {retry_count} >= max_retry {max_retry} - escalation required"
        human_required_reason = "Retry budget exhausted. User decision required to proceed."
        must_stop_triggers.append("retry_exhausted")
        return _build_result(
            state, reason, next_action, human_required_reason,
            must_stop_triggers, matched_continuation_signal
        )

    # =========================================================================
    # Rule 3: Scope expansion / uncovered runtime change → ask_user
    # =========================================================================
    # Check for scope expansion from context or message
    scope_expanded = False
    if context and context.get("new_scope_keywords"):
        must_stop_triggers.append("scope_expansion")
        scope_expanded = True
    if is_new_task(message):
        if "scope_expansion" not in must_stop_triggers:
            must_stop_triggers.append("scope_expansion")
        scope_expanded = True

    if scope_expanded:
        state = ASK_USER
        reason = "Scope expansion detected - user approval required"
        human_required_reason = "Scope expansion beyond approved plan requires user approval"
        return _build_result(
            state, reason, next_action, human_required_reason,
            must_stop_triggers, matched_continuation_signal
        )

    has_triggers, other_triggers = check_must_stop_triggers(message, context)
    if has_triggers:
        state = ASK_USER
        reason = "Must-stop condition detected - human decision required"
        must_stop_triggers.extend(other_triggers)

        # Set specific human_required_reason based on trigger
        # NOTE: scope_expansion is NOT handled here — it is resolved earlier
        #       in decide_continuation (lines 340-384) before this call.
        if "uncovered_runtime_change" in other_triggers:
            human_required_reason = "Runtime change not covered by DeveloperPrompt requires new plan"
        elif "classifier_semantic_change" in other_triggers:
            human_required_reason = "Classifier/routing change requires Architect review"
        elif "secrets_credentials" in other_triggers:
            human_required_reason = "Secrets/credentials involved - security review required"
        elif "ambiguous_repair_path" in other_triggers:
            human_required_reason = "Ambiguous repair path - user decision required"

        return _build_result(
            state, reason, next_action, human_required_reason,
            must_stop_triggers, matched_continuation_signal
        )

    # =========================================================================
    # Rule 4: No active plan → ask_user
    # =========================================================================
    if not active_plan:
        state = ASK_USER
        reason = "No active execution plan - user clarification needed"
        human_required_reason = "No active plan. Provide a plan or clarify task."
        return _build_result(
            state, reason, next_action, human_required_reason,
            must_stop_triggers, matched_continuation_signal
        )

    # =========================================================================
    # Rule 6: Checkpoint complete → report_only (timing contract: stop at checkpoint)
    # =========================================================================
    checkpoint_complete = active_plan.get("checkpoint_complete", False)
    next_step = active_plan.get("next_planned_step")

    if checkpoint_complete:
        state = REPORT_ONLY
        reason = "Checkpoint reached - reporting summary, no further steps approved"
        next_action = None
        return _build_result(
            state, reason, next_action, human_required_reason,
            must_stop_triggers, matched_continuation_signal
        )

    # =========================================================================
    # Rule 5: Active plan + continuation signal + safe next step → auto_continue
    # =========================================================================
    if is_continuation_signal(message):
        matched_continuation_signal = message.strip()

        # Check if there's a safe next step
        auto_continue_allowed = active_plan.get("auto_continue_allowed", False)

        if not next_step:
            # No next step defined → report_only
            state = REPORT_ONLY
            reason = "No next step defined in active plan - reporting current status"
            next_action = None
            return _build_result(
                state, reason, next_action, human_required_reason,
                must_stop_triggers, matched_continuation_signal
            )
        elif auto_continue_allowed:
            # Explicitly allowed - safe to continue
            state = AUTO_CONTINUE
            reason = f"Continuation signal detected with approved next step: {next_step}"
            next_action = next_step
            return _build_result(
                state, reason, next_action, human_required_reason,
                must_stop_triggers, matched_continuation_signal
            )
        else:
            # auto_continue not explicitly allowed - ask for confirmation
            state = ASK_USER
            reason = "Continuation signal received but auto_continue not explicitly allowed"
            human_required_reason = "Auto-continue requires explicit approval in plan"
            return _build_result(
                state, reason, next_action, human_required_reason,
                must_stop_triggers, matched_continuation_signal
            )

    # =========================================================================
    # Rule 7: No next step defined → report_only
    # =========================================================================
    if not next_step:
        state = REPORT_ONLY
        reason = "No next step defined in active plan - reporting current status"
        next_action = None
        return _build_result(
            state, reason, next_action, human_required_reason,
            must_stop_triggers, matched_continuation_signal
        )

    # =========================================================================
    # Default: ask_user
    # =========================================================================
    state = ASK_USER
    reason = "Cannot auto-determine continuation path"
    human_required_reason = "Provide explicit next step or approval"

    return _build_result(
        state, reason, next_action, human_required_reason,
        must_stop_triggers, matched_continuation_signal
    )


def _build_result(
    state: str,
    reason: str,
    next_action: Optional[str],
    human_required_reason: Optional[str],
    must_stop_triggers: list[str],
    matched_continuation_signal: Optional[str]
) -> dict:
    """Build standardized result dict."""
    return {
        "state": state,
        "reason": reason,
        "next_action": next_action,
        "human_required_reason": human_required_reason,
        "must_stop_triggers": must_stop_triggers,
        "matched_continuation_signal": matched_continuation_signal,
    }


# =============================================================================
# Parallel Work Package Evaluation
# =============================================================================

# Shared canonical runtime files that cannot be modified in parallel
SHARED_CANONICAL_FILES = [
    "scripts/intake_classify.py",
    "scripts/lane_select.py",
    "scripts/pool.py",
    "scripts/observability_report.py",
    "config/routing_map_v1.json",
    "opencode.json",
    ".opencode/opencode.json",
]


def _check_canonical_file_touch(pkg: dict, canonical_file: str) -> bool:
    """Return True if *pkg* touches *canonical_file*."""
    return canonical_file in pkg.get("affected_files", [])


def evaluate_parallel_work_packages(
    packages: list[dict],
    *,
    strict_non_canonical_conflicts: bool = False,
) -> dict:
    """
    Evaluate if multiple work packages can run in parallel.

    Args:
        packages: List of package dicts, each with:
            - id: str
            - worktree: str (optional, for separate worktrees)
            - branch: str (optional, for separate branches)
            - affected_files: list[str] (files touched by this package)
            - is_l4_operation: bool (default False)
            - validation_command: str (required for allowed)
            - description: str
        strict_non_canonical_conflicts:
            When True, non-canonical file conflicts (the same non-canonical
            file touched by multiple packages) block parallel execution.
            When False (default), they generate warnings only.

            **Motivation**: Strict mode is appropriate when the caller wants
            to guarantee zero write-conflict risk.  The default (safe/warn)
            is preferred during early-stage parallel exploration.

    Returns:
        dict with keys:
            - allowed: bool
            - reasons: list[str]
            - warnings: list[str]
            - blocked_packages: list[dict] (packages that cannot run in parallel)
            - parallel_packages: list[dict] (packages cleared for parallel execution)
    """
    warnings: list[str] = []

    if not packages:
        return {
            "allowed": False,
            "reasons": ["No packages provided"],
            "warnings": [],
            "blocked_packages": [],
            "parallel_packages": [],
        }

    if len(packages) == 1:
        # Single package - always allowed unless L4 or missing validation
        pkg = packages[0]
        pkg_id = pkg.get("id", "unknown")
        if pkg.get("is_l4_operation"):
            return {
                "allowed": False,
                "reasons": [f"Package {pkg_id} is L4 operation"],
                "warnings": [],
                "blocked_packages": [{"id": pkg_id, "reasons": ["L4 operation not allowed"]}],
                "parallel_packages": [],
            }
        if not pkg.get("validation_command"):
            return {
                "allowed": False,
                "reasons": [f"Package {pkg_id} missing validation command"],
                "warnings": [],
                "blocked_packages": [{"id": pkg_id, "reasons": ["Missing validation command"]}],
                "parallel_packages": [],
            }

        # Recommendation 7: Warn when a single package touches canonical files
        for canonical_file in SHARED_CANONICAL_FILES:
            if _check_canonical_file_touch(pkg, canonical_file):
                warnings.append(
                    f"Single package {pkg_id} touches canonical file "
                    f"{canonical_file}.  Ensure serial execution or "
                    f"coordinate with other agents."
                )

        return {
            "allowed": True,
            "reasons": ["Single package - no parallel conflicts"],
            "warnings": warnings,
            "blocked_packages": [],
            "parallel_packages": [pkg],
        }

    # Multiple packages - check for conflicts
    blocked_packages = []
    parallel_packages = []
    reasons = []

    # Check each package
    for pkg in packages:
        pkg_id = pkg.get("id", "unknown")
        pkg_blocked_reasons = []

        # 1. L4/protected operation check
        if pkg.get("is_l4_operation"):
            pkg_blocked_reasons.append("L4/protected operation not allowed in parallel")

        # 2. Missing validation command
        if not pkg.get("validation_command"):
            pkg_blocked_reasons.append("Missing validation command")

        # 3. Shared canonical files check (done below with cross-package analysis)
        if pkg_blocked_reasons:
            blocked_packages.append({
                "id": pkg_id,
                "reasons": pkg_blocked_reasons,
            })
        else:
            parallel_packages.append(pkg)

    # 4. Cross-package conflict detection
    # Build file ownership map
    file_to_packages: dict[str, list[str]] = {}

    for pkg in packages:
        pkg_id = pkg.get("id", "unknown")
        for file_path in pkg.get("affected_files", []):
            if file_path not in file_to_packages:
                file_to_packages[file_path] = []
            file_to_packages[file_path].append(pkg_id)

    # Check for shared canonical file conflicts
    # Shared canonical files are blocked for parallel modification ALWAYS
    for canonical_file in SHARED_CANONICAL_FILES:
        if canonical_file in file_to_packages:
            pkg_ids = file_to_packages[canonical_file]
            # Shared canonical files cannot be modified in parallel
            # Even a single package modifying them blocks parallel execution
            reasons.append(
                f"Shared canonical file: {canonical_file} requires serial execution. "
                f"Touched by: {pkg_ids}."
            )
            # Mark all packages touching canonical files as blocked
            for pkg in packages:
                pkg_id = pkg.get("id", "unknown")
                if pkg_id in pkg_ids:
                    # Check if already in blocked
                    existing = next(
                        (bp for bp in blocked_packages if bp["id"] == pkg_id),
                        None
                    )
                    if existing:
                        if "Shared canonical file" not in " ".join(existing["reasons"]):
                            existing["reasons"].append(f"Shared canonical file: {canonical_file}")
                    else:
                        blocked_packages.append({
                            "id": pkg_id,
                            "reasons": [f"Shared canonical file: {canonical_file}"],
                        })
                    # Remove from parallel if present
                    parallel_packages = [
                        p for p in parallel_packages if p.get("id") != pkg_id
                    ]

    # 5. Non-canonical file conflict detection (Recommendation 1)
    # Same non-canonical file touched by multiple packages — warn by default,
    # block only in strict mode.
    for file_path, pkg_ids in file_to_packages.items():
        if file_path in SHARED_CANONICAL_FILES:
            continue  # already handled above
        if len(pkg_ids) > 1:
            msg = (
                f"Non-canonical file conflict: {file_path} touched by "
                f"multiple packages {pkg_ids}. "
            )
            if strict_non_canonical_conflicts:
                msg += "Blocking due to strict mode."
                reasons.append(msg)
                for pkg in packages:
                    pkg_id = pkg.get("id", "unknown")
                    if pkg_id in pkg_ids:
                        existing = next(
                            (bp for bp in blocked_packages if bp["id"] == pkg_id),
                            None
                        )
                        if existing:
                            existing["reasons"].append(msg)
                        else:
                            blocked_packages.append({
                                "id": pkg_id,
                                "reasons": [msg],
                            })
                        parallel_packages = [
                            p for p in parallel_packages if p.get("id") != pkg_id
                        ]
            else:
                msg += "Safe mode: warning only. Set strict_non_canonical_conflicts=True to block."
                warnings.append(msg)

    # 6. Check for worktree/branch separation
    worktrees = set()
    branches = set()
    for pkg in packages:
        if pkg.get("worktree"):
            worktrees.add(pkg.get("worktree"))
        if pkg.get("branch"):
            branches.add(pkg.get("branch"))

    # If packages are in separate worktrees/branches, they can touch same files
    # This is tracked but doesn't automatically unblock shared file conflicts

    # Final decision
    if blocked_packages:
        return {
            "allowed": False,
            "reasons": reasons if reasons else ["One or more packages have conflicts"],
            "warnings": warnings,
            "blocked_packages": blocked_packages,
            "parallel_packages": parallel_packages,
        }

    return {
        "allowed": True,
        "reasons": [
            "All packages have independent file sets or separate worktrees/branches",
            "No L4 operations in parallel",
            "All packages have validation commands",
        ],
        "warnings": warnings,
        "blocked_packages": [],
        "parallel_packages": parallel_packages,
    }


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    """CLI entry point for continuation policy evaluation."""
    parser = argparse.ArgumentParser(
        description="Autonomous Continuation Policy Evaluator — v3.4 Phase C",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Evaluate continuation decision
  python3 scripts/continuation_policy.py --message "同意"

  # With active plan
  python3 scripts/continuation_policy.py --message "接下來要做什麼" \\
    --active-plan '{"id": "plan-001", "next_planned_step": "Create design doc", "auto_continue_allowed": true}'

  # With context
  python3 scripts/continuation_policy.py --message "release prod" \\
    --context '{"layer": "L4_release", "mandatory_delegation": true}'

  # Evaluate parallel packages
  python3 scripts/continuation_policy.py --mode parallel \\
    --packages '[{"id": "pkg1", "affected_files": ["docs/a.md"]}, {"id": "pkg2", "affected_files": ["docs/b.md"]}]'
        """
    )

    parser.add_argument("--message", "-m", help="User message to evaluate")
    parser.add_argument("--active-plan", type=json.loads, help="Active plan JSON")
    parser.add_argument("--context", type=json.loads, help="Context JSON")
    parser.add_argument(
        "--mode",
        choices=["decide", "parallel"],
        default="decide",
        help="Evaluation mode"
    )
    parser.add_argument("--packages", type=json.loads, help="Packages JSON for parallel evaluation")

    args = parser.parse_args()

    if args.mode == "parallel":
        packages = args.packages or []
        result = evaluate_parallel_work_packages(packages)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Decide mode
    if not args.message:
        print(json.dumps({
            "error": " --message is required for decide mode"
        }, indent=2))
        sys.exit(1)

    result = decide_continuation(args.message, args.active_plan, args.context)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
