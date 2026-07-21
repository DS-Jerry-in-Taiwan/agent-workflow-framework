#!/usr/bin/env python3
"""
OmO Boundary Dispatch Adapter — v3.7 Stream C

Read-only adapter that translates Native workflow L0-L4 decisions into
OmO-compatible dispatch payloads. This is the single bridge between Native
and OmO execution modes.

Design references:
  - evaluation_plan.md §7 (adapter boundary spec)
  - evaluation_plan.md §6.1 (L4 exclusion mechanism)

This module:
  - Reads pool item data (WorkflowDecisionContract)
  - Translates L0-L3 layers to OmO category ranges
  - Rejects L4 tasks with OmOL4NotAllowedError
  - Sets runtime_mode="omo" in output payload
  - Applies governance_constraints per layer

Forbidden (hard boundaries):
  - MUST NOT import intake_classify.py or lane_select.py
  - MUST NOT route L4 tasks
  - MUST NOT call agent-qa or agent-releaser
  - MUST NOT write directly to canonical pool state
"""

from dataclasses import dataclass


class OmOL4NotAllowedError(Exception):
    """Raised when an L4 task is attempted through OmO dispatch."""
    pass


@dataclass
class WorkflowDecisionContract:
    """Input contract: parsed from pool item dict."""
    item_id: str
    original_request: str
    final_layer: str          # L0-L4
    confidence: float
    mode: str                 # "direct" | "guarded" | "clarify"
    l4_mandatory_delegation: bool
    lane: str
    required_agents: list[str]


@dataclass
class OmODispatchPayload:
    """Output payload: consumed by Atlas/Sisyphus-Junior."""
    item_id: str
    omo_category: str          # "quick" | "deep" | "ultrabrain" | "visual-engineering" | "writing"
    original_request: str
    runtime_mode: str          # always "omo"
    governance_constraints: list[str]


# Translation table: L0-L4 layer → primary OmO category (first in allowed range).
# Reference: evaluation_plan.md §7.2
_LAYER_TO_OMO_CATEGORY = {
    "L0_config_housekeeping": "quick",     # quick | writing
    "L1_feature_dev": "deep",              # deep | visual-engineering
    "L2_bug_fix": "quick",                # quick | deep
    "L3_refactor": "ultrabrain",           # ultrabrain | deep
    # L4_release handled separately (rejection)
}


def translate_layer_to_omo_category(final_layer: str) -> str:
    """
    Translate a Native L0-L4 layer to the primary OmO category.

    Unknown layers fall back to "deep" with a warning.
    L4_release is NOT handled here — caller must raise before reaching this.
    """
    if final_layer in _LAYER_TO_OMO_CATEGORY:
        return _LAYER_TO_OMO_CATEGORY[final_layer]

    # Unknown layer: safe fallback
    import sys
    print(
        f"WARNING: Unknown layer {final_layer!r}, falling back to 'deep' category.",
        file=sys.stderr,
    )
    return "deep"


def build_dispatch_payload(contract: WorkflowDecisionContract) -> OmODispatchPayload:
    """
    Build an OmO dispatch payload from a WorkflowDecisionContract.

    Raises:
        OmOL4NotAllowedError: If final_layer is L4_release.
    """
    # L4 exclusion: non-negotiable boundary
    if contract.final_layer == "L4_release":
        raise OmOL4NotAllowedError(
            "L4 tasks require agent-releaser and must use Native workflow. "
            "OmO mode cannot route L4 tasks."
        )

    # Translate layer to OmO category
    omo_category = translate_layer_to_omo_category(contract.final_layer)

    # Build governance constraints per layer
    governance_constraints = ["no_l4", "no_cross_mode_validate"]

    if contract.final_layer == "L3_refactor":
        governance_constraints.append("requires_human_approval")

    # Unknown layer (not in translation table, not L4)
    if contract.final_layer not in _LAYER_TO_OMO_CATEGORY:
        governance_constraints.append("requires_architect_review")

    return OmODispatchPayload(
        item_id=contract.item_id,
        omo_category=omo_category,
        original_request=contract.original_request,
        runtime_mode="omo",
        governance_constraints=governance_constraints,
    )


def contract_from_dict(data: dict) -> WorkflowDecisionContract:
    """
    Construct a WorkflowDecisionContract from a pool item dict (or subset).

    Field mapping:
      item_id           ← data.get("id", "unknown")
      original_request  ← data.get("title", "")
      final_layer       ← data.get("classifier_result", {}).get("final_layer", "unknown")
      confidence        ← data.get("classifier_result", {}).get("confidence", 0.0)
      mode              ← data.get("classifier_result", {}).get("mode", "clarify")
      l4_mandatory_delegation ← data.get("lane_decision", {}).get("l4_mandatory_delegation", False)
      lane              ← data.get("lane_decision", {}).get("lane", "unknown")
      required_agents   ← data.get("lane_decision", {}).get("required_agents", [])
    """
    classifier_result = data.get("classifier_result", {})
    lane_decision = data.get("lane_decision", {})

    return WorkflowDecisionContract(
        item_id=data.get("id", "unknown"),
        original_request=data.get("title", ""),
        final_layer=classifier_result.get("final_layer", "unknown"),
        confidence=classifier_result.get("confidence", 0.0),
        mode=classifier_result.get("mode", "clarify"),
        l4_mandatory_delegation=lane_decision.get("l4_mandatory_delegation", False),
        lane=lane_decision.get("lane", "unknown"),
        required_agents=lane_decision.get("required_agents", []),
    )


def validate_omo_dispatch(payload: OmODispatchPayload) -> tuple[bool, list[str]]:
    """
    Validate an OmODispatchPayload for dispatch readiness.

    Returns (True, []) if valid, (False, [error, ...]) otherwise.

    Checks:
      - omo_category is a known OmO category
      - runtime_mode is "omo"
      - governance_constraints is non-empty
      - no_l4 constraint is present
    """
    errors: list[str] = []
    valid_categories = {"quick", "deep", "ultrabrain", "visual-engineering", "writing"}

    if payload.omo_category not in valid_categories:
        errors.append(
            f"omo_category {payload.omo_category!r} is not a known category; "
            f"expected one of {sorted(valid_categories)}"
        )

    if payload.runtime_mode != "omo":
        errors.append(f"runtime_mode must be 'omo', got {payload.runtime_mode!r}")

    if not payload.governance_constraints:
        errors.append("governance_constraints is empty; at least 'no_l4' is required")

    if "no_l4" not in payload.governance_constraints:
        errors.append("governance_constraints must include 'no_l4'")

    return len(errors) == 0, errors
