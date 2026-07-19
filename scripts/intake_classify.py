#!/usr/bin/env python3
"""
Intake Classifier Script — Phase v2.1 Runtime MVP

Reads routing_map_v1.json and classifies user requests into L0-L4 layers.
Implements canonical confidence formula: 0.65 * margin_component + 0.35 * ratio_component
Thresholds: >=0.85 direct, >=0.55 guarded, <0.55 clarify
Dominance: L4_release > L3_refactor > L2_bug_fix > L1_feature_dev > L0_config_housekeeping

Usage:
    python3 scripts/intake_classify.py "fix threshold bug"
    echo "release prod" | python3 scripts/intake_classify.py

L4 responsibility: When the classifier outputs ``final_layer=L4_release``, the task MUST be delegated
to ``agent-releaser``. The Architect must NOT execute release, deploy, tag, merge, push-to-main, or
any production operation directly. See ``lane_select.lane_selector()`` for the ZERO_BYPASS
enforcement point.
"""

import json
import sys
from pathlib import Path

# Canonical constants (must not be modified)
CONFIDENCE_FORMULA = "0.65 * margin_component + 0.35 * ratio_component"
THRESHOLD_DIRECT = 0.85
THRESHOLD_GUARDED = 0.55
DOMINANCE_ORDER = ["L4_release", "L3_refactor", "L2_bug_fix", "L1_feature_dev", "L0_config_housekeeping"]
LAYER_TO_DOMINANCE = {
    "L0_config_housekeeping": "L0_config_housekeeping",
    "L1_feature_dev": "L1_feature_dev",
    "L2_bug_fix": "L2_bug_fix",
    "L3_refactor": "L3_refactor",
    "L4_release": "L4_release",
}


def load_routing_map(map_path: str) -> dict:
    """Load and validate routing_map_v1.json."""
    with open(map_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_input_text() -> str:
    """Get input text from CLI args or stdin."""
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:])
    # Read from stdin
    return sys.stdin.read().strip()


def normalize_text(text: str) -> str:
    """Normalize input text for matching."""
    return text.lower()


def compute_keyword_scores(text: str, routing_map: dict) -> dict:
    """
    Compute keyword match scores per layer.
    Returns dict: {layer_name: {"match_count": int, "total_keywords": int, "matched_keywords": [str]}}
    
    Data contract — consumed by downstream modules:
      - lane_select.py reads `classifier_result: {final_layer, confidence, conflict_status}`.
      - pool.py stores `classifier_result` dict as-is in item schema.
      - observability_report.py reads `classifier_result.final_layer` as layer fallback.
    """
    normalized = normalize_text(text)
    words = set(normalized.split())
    
    scores = {}
    for layer_name, layer_data in routing_map.get("layers", {}).items():
        keywords = layer_data.get("keywords", [])
        matched = [kw for kw in keywords if kw.lower() in normalized]
        
        scores[layer_name] = {
            "match_count": len(matched),
            "total_keywords": len(keywords),
            "matched_keywords": matched,
        }
    
    return scores


def compute_confidence(scores: dict) -> tuple:
    """
    Compute confidence using canonical formula.
    Returns: (confidence, top_layer, second_layer, margin_component, ratio_component)
    """
    # Sort layers by match count
    sorted_layers = sorted(
        scores.items(),
        key=lambda x: (x[1]["match_count"], x[0]),
        reverse=True
    )
    
    if not sorted_layers or sorted_layers[0][1]["match_count"] == 0:
        return (0.0, None, None, 0.0, 0.0)
    
    top_layer, top_data = sorted_layers[0]
    second_layer, second_data = (sorted_layers[1] if len(sorted_layers) > 1 and sorted_layers[1][1]["match_count"] > 0 else (None, {"match_count": 0}))
    
    top_score = top_data["match_count"]
    second_score = second_data["match_count"]
    
    # Canonical formula: confidence = 0.65 * margin_component + 0.35 * ratio_component
    # margin_component = (top_score - second_score) / top_score
    # ratio_component = top_match_count / top_total_keywords
    
    if top_score > 0:
        margin_component = (top_score - second_score) / top_score
        ratio_component = top_score / top_data["total_keywords"]
    else:
        margin_component = 0.0
        ratio_component = 0.0
    
    confidence = 0.65 * margin_component + 0.35 * ratio_component
    
    return (confidence, top_layer, second_layer, margin_component, ratio_component)


def apply_dominance(matched_layers: list) -> str:
    """
    Apply cross-layer dominance when multiple layers match.
    Higher risk layer wins: L4_release > L3_refactor > L2_bug_fix > L1_feature_dev > L0_config_housekeeping
    """
    if not matched_layers:
        return None
    
    for dominance_layer in DOMINANCE_ORDER:
        for layer in matched_layers:
            if layer == dominance_layer:
                return layer
    
    # Return highest priority from matched
    return matched_layers[0]


def classify(input_text: str, routing_map: dict) -> dict:
    """
    Main classification logic.
    Returns structured classifier result dict with fields consumed by downstream:
      - final_layer/confidence/mode: lane_select.py, pool.py, observability_report.py
      - l4_mandatory_delegation: lane_select.py, observability_report.py
      - dominance_applied: debug/audit
      - l4_mandatory_delegation: True when final_layer == "L4_release". Critical input to
        lane_select.lane_selector() for zero-bypass enforcement.
    """
    scores = compute_keyword_scores(input_text, routing_map)
    
    # Find all layers with matches
    matched_layers = [layer for layer, data in scores.items() if data["match_count"] > 0]
    
    # Compute confidence
    confidence, top_layer, second_layer, margin_comp, ratio_comp = compute_confidence(scores)
    
    # Apply dominance
    final_layer = apply_dominance(matched_layers) if matched_layers else None
    
    # Determine mode
    if final_layer is None:
        mode = "clarify"
    elif confidence >= THRESHOLD_DIRECT:
        mode = "direct"
    elif confidence >= THRESHOLD_GUARDED:
        mode = "guarded"
    else:
        mode = "clarify"
    
    # L4 mandatory delegation
    l4_mandatory_delegation = (final_layer == "L4_release")
    
    # Generate next_step recommendation
    next_step = generate_next_step(final_layer, mode, confidence)
    
    # Top and second scores
    top_score = scores[top_layer]["match_count"] if top_layer else 0
    second_score = scores[second_layer]["match_count"] if second_layer else 0
    
    # Dominance applied if multiple layers matched and top is not final
    dominance_applied = len(matched_layers) > 1 and final_layer != top_layer
    
    return {
        "input": input_text,
        "final_layer": final_layer,
        "confidence": round(confidence, 4),
        "mode": mode,
        "matched_keywords": {layer: data["matched_keywords"] for layer, data in scores.items() if data["matched_keywords"]},
        "top_score": top_score,
        "second_score": second_score,
        "dominance_applied": dominance_applied,
        "next_step": next_step,
        "l4_mandatory_delegation": l4_mandatory_delegation,
        # Internal metadata for debugging
        "_debug": {
            "margin_component": round(margin_comp, 4),
            "ratio_component": round(ratio_comp, 4),
            "matched_layers": matched_layers,
            "formula": CONFIDENCE_FORMULA,
        }
    }


def generate_next_step(final_layer: str, mode: str, confidence: float) -> str:
    """Generate human-readable next step recommendation."""
    if final_layer is None or mode == "clarify":
        return "Ask clarifying question: 'Is this a config change, new feature, bug fix, refactoring, or release task?'"
    
    if final_layer == "L4_release":
        return "Delegate to agent-releaser. Architect must not execute release/deploy/tag/merge operations."
    
    if final_layer == "L3_refactor":
        return "Architect planning required. Pre-approval needed before development."
    
    if final_layer == "L2_bug_fix":
        return "Developer implements fix. QA validates. Architect spot-checks validate report."
    
    if final_layer == "L1_feature_dev":
        return "Architect planning. Developer implements. QA validates."
    
    if final_layer == "L0_config_housekeeping":
        return "Developer implements. Auto-approve if tests pass. No Validate Gate required for L0 Fast Track."
    
    return "Route to appropriate lane based on layer classification."


def main():
    """Main entry point."""
    # Determine routing_map path
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    routing_map_path = repo_root / "docs" / "intake_layer" / "routing_map_v1.json"
    
    if not routing_map_path.exists():
        print(json.dumps({
            "error": f"routing_map_v1.json not found at {routing_map_path}"
        }, indent=2))
        sys.exit(1)
    
    # Get input
    input_text = get_input_text()
    if not input_text:
        print(json.dumps({
            "error": "No input provided. Pass text as argument or via stdin."
        }, indent=2))
        sys.exit(1)
    
    # Load routing map
    routing_map = load_routing_map(str(routing_map_path))
    
    # Classify
    result = classify(input_text, routing_map)
    
    # Output JSON (without debug fields for clean output)
    output = {k: v for k, v in result.items() if not k.startswith("_")}
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
