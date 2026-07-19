#!/usr/bin/env python3
"""
v3.3 AgEnD Message Adapter — Phase 4 Minimal Adapter Spike

Local-only adapter formatter that validates a Workflow Decision Contract,
applies governance pre-dispatch guards, and formats a safe dispatch payload.

Pure functions, stdlib only, no network calls, no live AgEnD integration.

Exports:
    validate_workflow_decision(decision: dict) -> tuple[bool, list[str]]
    governance_pre_dispatch(decision: dict) -> tuple[bool, str | None]
    format_dispatch_payload(decision: dict) -> dict

Scope: This module is a **local-only mock/simulation layer**. It validates decision contracts and
formats payloads that a future live AgEnD dispatcher would send. It does NOT:
  - Connect to any AgEnD service or external dispatcher
  - Make network calls
  - Persist dispatch state to any queue or database
  - Execute or route any task to a remote agent

For production use, an AgEnD client or similar dispatcher would consume the output of
``format_dispatch_payload()``.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Canonical constants (must stay in sync with docs/intake_layer/routing_map_v1.json)
# ---------------------------------------------------------------------------

KNOWN_LAYERS: tuple[str, ...] = (
    "L0_config_housekeeping",
    "L1_feature_dev",
    "L2_bug_fix",
    "L3_refactor",
    "L4_release",
)

KNOWN_LANES: tuple[str, ...] = (
    "L0_Fast_Track",
    "L1_Standard",
    "L2_QuickFix",
    "L2_Investigate",
    "L3_HighRisk",
    "L4_Releaser",
)

VALID_RISK_LEVELS: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH")

VALID_HITL_MODES: tuple[str, ...] = ("auto_approve", "review", "pre_approval")

VALID_PAYLOAD_KINDS: tuple[str, ...] = ("task", "review", "handoff", "none")

VALID_TARGET_RUNTIMES: tuple[str, ...] = ("native", "agend", "agend-terminal", "none")

AGENT_RELEASER: str = "agent-releaser"

L4_LANE: str = "L4_Releaser"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUEST_ID_PATTERN: re.Pattern = re.compile(r"^workflow-[0-9]{8}-[0-9]{3}$")


def _coerce_required_string(
    obj: dict[str, Any], key: str, errors: list[str],
) -> str | None:
    """Get a required string field, appending an error if missing or invalid."""
    val = obj.get(key)
    if not isinstance(val, str) or not val.strip():
        errors.append(f"Missing or empty required field: {key}")
        return None
    return val


def _coerce_required_bool(
    obj: dict[str, Any], key: str, errors: list[str],
) -> bool | None:
    """Get a required boolean field, appending an error if missing or invalid."""
    val = obj.get(key)
    if not isinstance(val, bool):
        errors.append(f"Missing or non-boolean required field: {key}")
        return None
    return val


# ---------------------------------------------------------------------------
# 1. validate_workflow_decision
# ---------------------------------------------------------------------------


def validate_workflow_decision(decision: dict) -> tuple[bool, list[str]]:
    """
    Validate the shape of a Workflow Decision Contract.

    Checks:
    - Top-level required fields are present and typed.
    - contract_version is 'v3.3-wave1'.
    - request_id matches ``workflow-YYYYMMDD-NNN`` pattern.
    - classifier_result includes final_layer / confidence / mode.
    - lane_decision includes lane / required_agents / bypass_risk.
    - governance includes safe_to_dispatch.
    - adapter_dispatch includes target_runtime / dispatch_allowed / dispatch_payload_kind.

    Returns (True, []) for valid input, (False, [error, ...]) otherwise.
    """
    errors: list[str] = []

    # --- Top-level shape ---
    if not isinstance(decision, dict):
        return False, ["decision is not a dict"]

    # contract_version
    cv = _coerce_required_string(decision, "contract_version", errors)
    if cv is not None and cv != "v3.3-wave1":
        errors.append(f"contract_version must be 'v3.3-wave1', got {cv!r}")

    # request_id format
    rid = _coerce_required_string(decision, "request_id", errors)
    if rid is not None and not _REQUEST_ID_PATTERN.match(rid):
        errors.append(f"request_id {rid!r} does not match workflow-YYYYMMDD-NNN")

    # original_request
    _coerce_required_string(decision, "original_request", errors)

    # --- classifier_result ---
    cr = decision.get("classifier_result")
    if not isinstance(cr, dict):
        errors.append("classifier_result is missing or not a dict")
    else:
        fl = cr.get("final_layer")
        if fl is not None and fl not in KNOWN_LAYERS:
            errors.append(
                f"classifier_result.final_layer {fl!r} is not a known layer"
            )
        conf = cr.get("confidence")
        if not isinstance(conf, (int, float)):
            errors.append("classifier_result.confidence is missing or not numeric")
        else:
            if conf < 0.0 or conf > 1.0:
                errors.append(
                    f"classifier_result.confidence {conf} is outside [0, 1]"
                )
        mode = cr.get("mode")
        if mode not in ("direct", "guarded", "clarify"):
            errors.append(f"classifier_result.mode {mode!r} is not valid")
        _coerce_required_bool(cr, "l4_mandatory_delegation", errors)

    # --- execution_contract ---
    ec = decision.get("execution_contract")
    if isinstance(ec, dict):
        _coerce_required_string(ec, "clarified_spec", errors)
        rl = ec.get("risk_level")
        if rl is not None and rl not in VALID_RISK_LEVELS:
            errors.append(f"execution_contract.risk_level {rl!r} is not valid")
    else:
        errors.append("execution_contract is missing or not a dict")

    # --- lane_decision ---
    ld = decision.get("lane_decision")
    if not isinstance(ld, dict):
        errors.append("lane_decision is missing or not a dict")
    else:
        lane = ld.get("lane")
        if lane is not None and lane not in KNOWN_LANES:
            errors.append(f"lane_decision.lane {lane!r} is not a known lane")
        _coerce_required_string(ld, "bypass_risk", errors)
        _coerce_required_bool(ld, "qa_required", errors)
        _coerce_required_bool(ld, "hitl_required", errors)
        hm = ld.get("hitl_mode")
        if hm is not None and hm not in VALID_HITL_MODES:
            errors.append(f"lane_decision.hitl_mode {hm!r} is not valid")

    # --- governance ---
    gv = decision.get("governance")
    if not isinstance(gv, dict):
        errors.append("governance is missing or not a dict")
    else:
        _coerce_required_bool(gv, "safe_to_dispatch", errors)

    # --- adapter_dispatch ---
    ad = decision.get("adapter_dispatch")
    if not isinstance(ad, dict):
        errors.append("adapter_dispatch is missing or not a dict")
    else:
        tr = ad.get("target_runtime")
        if tr is not None and tr not in VALID_TARGET_RUNTIMES:
            errors.append(
                f"adapter_dispatch.target_runtime {tr!r} is not valid"
            )
        _coerce_required_bool(ad, "dispatch_allowed", errors)
        pk = ad.get("dispatch_payload_kind")
        if pk is not None and pk not in VALID_PAYLOAD_KINDS:
            errors.append(
                f"adapter_dispatch.dispatch_payload_kind {pk!r} is not valid"
            )

    # --- evidence ---
    ev = decision.get("evidence")
    if not isinstance(ev, dict):
        errors.append("evidence is missing or not a dict")

    if errors:
        return False, errors
    return True, []


# ---------------------------------------------------------------------------
# 2. governance_pre_dispatch
# ---------------------------------------------------------------------------


def governance_pre_dispatch(decision: dict) -> tuple[bool, str | None]:
    """
    Apply governance pre-dispatch guard.

    Blocks (returns (False, reason)) if any of:
    - classifier_result.mode == 'clarify'
    - classifier_result.final_layer is None/unknown
    - lane_decision.lane is None/unknown
    - governance.safe_to_dispatch is not True
    - L4 lane but lane_decision.l4_mandatory_delegation is not True
    - L4 lane but governance.required_handoff != 'agent-releaser'
    - L4 lane but target_agent (from adapter_dispatch) is not 'agent-releaser'
    - L4 lane but lane_decision.hitl_required is not True

    Otherwise returns (True, None) meaning dispatch may proceed.
    """
    # -- classifier_result --
    cr = decision.get("classifier_result", {})
    mode = cr.get("mode")
    if mode == "clarify":
        return False, "CLARIFY_MODE — request requires clarification before dispatch"

    final_layer = cr.get("final_layer")
    if final_layer is None:
        return False, "UNKNOWN_LAYER — classifier_result.final_layer is None"
    if final_layer not in KNOWN_LAYERS:
        return False, f"UNKNOWN_LAYER — {final_layer!r} is not a known layer"

    # -- lane_decision --
    ld = decision.get("lane_decision", {})
    lane = ld.get("lane")
    if lane is None:
        return False, "UNKNOWN_LANE — lane_decision.lane is None"
    if lane not in KNOWN_LANES:
        return False, f"UNKNOWN_LANE — {lane!r} is not a known lane"

    # -- governance --
    gv = decision.get("governance", {})
    if gv.get("safe_to_dispatch") is not True:
        return False, "GOVERNANCE_BLOCKED — governance.safe_to_dispatch is not True"

    # -- L4-specific guards --
    if lane == L4_LANE:
        if ld.get("l4_mandatory_delegation") is not True:
            return (
                False,
                "ZERO_BYPASS_L4_GUARD — L4 lane without l4_mandatory_delegation=True",
            )
        if gv.get("required_handoff") != AGENT_RELEASER:
            return (
                False,
                "ZERO_BYPASS_L4_GUARD — L4 lane without required_handoff='agent-releaser'",
            )
        if ld.get("hitl_required") is not True:
            return (
                False,
                "ZERO_BYPASS_L4_GUARD — L4 lane without hitl_required=True",
            )
        # Check target_agent from adapter_dispatch
        ad = decision.get("adapter_dispatch", {})
        target_agent = ad.get("target_agent")
        if target_agent is not None and target_agent != AGENT_RELEASER:
            return (
                False,
                f"ZERO_BYPASS_L4_GUARD — L4 target_agent={target_agent!r} is not {AGENT_RELEASER!r}",
            )

    return True, None


# ---------------------------------------------------------------------------
# 3. format_dispatch_payload
# ---------------------------------------------------------------------------


def format_dispatch_payload(decision: dict) -> dict:
    """
    Format a safe dispatch payload from a validated Workflow Decision Contract.

    Precondition: ``validate_workflow_decision`` and ``governance_pre_dispatch``
    must have passed before calling this function.

    Returns a dict with the portable dispatch payload shape:

    .. code-block:: json

        {
          "request_kind": "task|handoff",
          "requires_reply": true,
          "correlation_id": "...",
          "target_agent": "...",
          "task_summary": "...",
          "instructions": "...",
          "metadata": {
            "lane": "...",
            "required_agents": [],
            "qa_required": bool,
            "hitl_required": bool,
            "hitl_mode": "...",
            "l4_mandatory_delegation": bool,
            "bypass_risk": "..."
          }
        }

    For L4_Releaser lanes:
    - ``request_kind`` is ``"handoff"``
    - ``target_agent`` is ``"agent-releaser"``

    For L0–L3 lanes:
    - ``request_kind`` is ``"task"``
    - ``target_agent`` is derived from required_agents (first entry or fallback)
    """
    # -- Input derivation --
    request_id = decision.get("request_id", "workflow-00000000-000")
    original_request = decision.get("original_request", "")

    ld: dict = decision.get("lane_decision", {})
    lane: str = ld.get("lane", "L1_Standard")
    required_agents: list[str] = ld.get("required_agents", [])
    qa_required: bool = bool(ld.get("qa_required", True))
    hitl_required: bool = bool(ld.get("hitl_required", False))
    hitl_mode: str = ld.get("hitl_mode", "review")
    l4_delegation: bool = bool(ld.get("l4_mandatory_delegation", False))
    bypass_risk: str = ld.get("bypass_risk", "")

    ad: dict = decision.get("adapter_dispatch", {})
    correlation_id: str = ad.get("correlation_id") or request_id
    payload_kind: str = ad.get("dispatch_payload_kind", "task")

    gv: dict = decision.get("governance", {})

    # -- Determine request_kind and target_agent --
    if lane == L4_LANE:
        request_kind = "handoff"
        target_agent = AGENT_RELEASER
    else:
        request_kind = "task"
        target_agent = required_agents[0] if required_agents else "agent-developer"

    # -- Build metadata --
    metadata: dict[str, Any] = {
        "lane": lane,
        "required_agents": required_agents,
        "qa_required": qa_required,
        "hitl_required": hitl_required,
        "hitl_mode": hitl_mode,
        "l4_mandatory_delegation": l4_delegation,
        "bypass_risk": bypass_risk,
    }

    # Optional: propagate continuation_decision into metadata
    # (v3.5 backward-compatible extension; no existing key is removed)
    cd: Any = decision.get("continuation_decision")
    if isinstance(cd, dict):
        metadata["continuation_state"] = cd.get("state", "unknown")
        metadata["continuation_next_action"] = cd.get("next_action")
        metadata["must_stop_triggers"] = cd.get("must_stop_triggers", [])

    # Build instructions from available context
    instructions_parts: list[str] = []
    ec = decision.get("execution_contract", {})
    if ec.get("clarified_spec"):
        instructions_parts.append(
            f"Spec: {ec['clarified_spec']}"
        )
    sc = ec.get("success_criteria", [])
    if sc:
        instructions_parts.append("Success criteria: " + "; ".join(sc))
    vp = ec.get("validation_plan", [])
    if vp:
        instructions_parts.append("Validation: " + "; ".join(vp))
    forbidden = gv.get("forbidden_actions", [])
    if forbidden:
        instructions_parts.append("Forbidden actions: " + "; ".join(forbidden))
    instructions = "\n".join(instructions_parts)

    return {
        "request_kind": request_kind,
        "requires_reply": True,
        "correlation_id": correlation_id,
        "target_agent": target_agent,
        "task_summary": original_request,
        "instructions": instructions,
        "metadata": metadata,
    }
