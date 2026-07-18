#!/usr/bin/env python3
"""
Lane Selector Policy Runner — Phase v2.1 Runtime MVP

Consumes execution_contract + classifier_result and outputs lane decision.
Implements v0.4 Lane Selector policy logic for L0/L1/L2/L3/L4.

Usage:
    # With JSON file input:
    python3 scripts/lane_select.py input.json
    
    # With sample fixtures:
    python3 scripts/lane_select.py --sample L0_Fast_Track
    python3 scripts/lane_select.py --sample L2_QuickFix
    python3 scripts/lane_select.py --sample L4_RELEASE

Sample Inputs (JSON format):
{
  "execution_contract": {
    "clarified_spec": "string",
    "scope_boundary": {"in_scope": [], "out_of_scope": []},
    "success_criteria": [],
    "validation_plan": [],
    "risk_level": "LOW|MEDIUM|HIGH",
    "recommended_layer": "L0|L1|L2|L3|L4",
    "next_step": "string",
    "residual_ambiguity": []
  },
  "classifier_result": {
    "final_layer": "L0|L1|L2|L3|L4",
    "confidence": 0.0-1.0,
    "conflict_status": "aligned|conflict_reviewed|scorer_dominance"
  }
}

Lane Decision Output:
{
  "lane": "L0_Fast_Track|L1_Standard|L2_QuickFix|L2_Investigate|L3_HighRisk|L4_Releaser",
  "escalation_triggered": boolean,
  "escalation_reason": string|null,
  "required_agents": ["Agent1", "Agent2"],
  "qa_required": boolean,
  "hitl_required": boolean,
  "hitl_mode": "auto_approve|review|pre_approval",
  "l4_mandatory_delegation": boolean,
  "bypass_risk": "string"
}
"""

import argparse
import json
import sys

# Valid lanes (from v0.4 spec)
LANE_L0_FAST_TRACK = "L0_Fast_Track"
LANE_L1_STANDARD = "L1_Standard"
LANE_L2_QUICK_FIX = "L2_QuickFix"
LANE_L2_INVESTIGATE = "L2_Investigate"
LANE_L3_HIGH_RISK = "L3_HighRisk"
LANE_L4_RELEASER = "L4_Releaser"

# L4 mandatory delegation agent
AGENT_RELEASER = "agent-releaser"

# Sample fixtures
SAMPLE_FIXTURES = {
    "L0_Fast_Track": {
        "description": "L0 config housekeeping with all Fast Track eligibility met",
        "execution_contract": {
            "clarified_spec": "Update ruff.toml to add new lint rule",
            "scope_boundary": {
                "in_scope": ["ruff.toml"],
                "out_of_scope": ["src/", "tests/", "serverless.yml", "release.json"]
            },
            "success_criteria": ["Ruff passes with new rule"],
            "validation_plan": ["Run ruff check"],
            "risk_level": "LOW",
            "recommended_layer": "L0_config_housekeeping",
            "next_step": "Update ruff.toml",
            "residual_ambiguity": []
        },
        "classifier_result": {
            "final_layer": "L0_config_housekeeping",
            "confidence": 0.92,
            "conflict_status": "aligned"
        }
    },
    "L2_QuickFix": {
        "description": "L2 bug fix with known root cause and regression validation",
        "execution_contract": {
            "clarified_spec": "Fix false positive in threshold check - threshold value too low",
            "scope_boundary": {
                "in_scope": ["src/quality/checks.py"],
                "out_of_scope": ["Search strategy changes", "New features"]
            },
            "success_criteria": [
                "Existing tests pass",
                "False positive rate < 5%",
                "No new false negatives"
            ],
            "validation_plan": [
                "Run existing test suite",
                "Manual verification of sample cases"
            ],
            "risk_level": "MEDIUM",
            "recommended_layer": "L2_bug_fix",
            "next_step": "Developer implements fix",
            "residual_ambiguity": []
        },
        "classifier_result": {
            "final_layer": "L2_bug_fix",
            "confidence": 0.87,
            "conflict_status": "aligned"
        }
    },
    "L2_Investigate": {
        "description": "L2 bug with unknown root cause, needs investigation",
        "execution_contract": {
            "clarified_spec": "Investigate intermittent crash in parallel processing",
            "scope_boundary": {
                "in_scope": ["src/services/*.py"],
                "out_of_scope": ["Database layer", "Frontend"]
            },
            "success_criteria": [
                "Identify root cause",
                "Implement fix",
                "Add regression test"
            ],
            "validation_plan": [
                "Reproduce crash",
                "Add debug logging",
                "Create regression test"
            ],
            "risk_level": "MEDIUM",
            "recommended_layer": "L2_bug_fix",
            "next_step": "Debugger investigates",
            "residual_ambiguity": ["Root cause unknown", "Intermittent timing issue?"]
        },
        "classifier_result": {
            "final_layer": "L2_bug_fix",
            "confidence": 0.71,
            "conflict_status": "aligned"
        }
    },
    "L4_RELEASE": {
        "description": "L4 release task - must delegate to releaser",
        "execution_contract": {
            "clarified_spec": "Release version v1.2.3 to production",
            "scope_boundary": {
                "in_scope": ["release.json", "CHANGELOG.md", "tag v1.2.3"],
                "out_of_scope": ["Code changes", "New features"]
            },
            "success_criteria": [
                "Tag created",
                "Release notes generated",
                "CI/CD pipeline completes",
                "Healthcheck passes"
            ],
            "validation_plan": [
                "Verify tag format",
                "Check release.json consistency",
                "Confirm ancestry in main",
                "Human approval"
            ],
            "risk_level": "HIGH",
            "recommended_layer": "L4_release",
            "next_step": "Delegate to agent-releaser",
            "residual_ambiguity": []
        },
        "classifier_result": {
            "final_layer": "L4_release",
            "confidence": 0.95,
            "conflict_status": "aligned"
        }
    },
    "L1_Feature": {
        "description": "L1 feature development",
        "execution_contract": {
            "clarified_spec": "Add new Tavily search provider",
            "scope_boundary": {
                "in_scope": ["src/services/search/", "config/providers.json"],
                "out_of_scope": ["Other search providers", "UI changes"]
            },
            "success_criteria": [
                "New provider integrated",
                "Tests pass",
                "Documentation updated"
            ],
            "validation_plan": [
                "Unit tests",
                "Integration tests",
                "Manual verification"
            ],
            "risk_level": "MEDIUM",
            "recommended_layer": "L1_feature_dev",
            "next_step": "Architect reviews design",
            "residual_ambiguity": []
        },
        "classifier_result": {
            "final_layer": "L1_feature_dev",
            "confidence": 0.88,
            "conflict_status": "aligned"
        }
    },
    "L3_Refactor": {
        "description": "L3 refactoring task",
        "execution_contract": {
            "clarified_spec": "Extract abstract base class for processors",
            "scope_boundary": {
                "in_scope": ["src/processors/*.py"],
                "out_of_scope": ["API changes", "Database schema"]
            },
            "success_criteria": [
                "Abstract base created",
                "All processors inherit correctly",
                "Tests pass"
            ],
            "validation_plan": [
                "Full test suite",
                "Architecture review",
                "Pre-approval required"
            ],
            "risk_level": "HIGH",
            "recommended_layer": "L3_refactor",
            "next_step": "Architect planning",
            "residual_ambiguity": []
        },
        "classifier_result": {
            "final_layer": "L3_refactor",
            "confidence": 0.91,
            "conflict_status": "aligned"
        }
    }
}


def check_l0_fast_track_eligibility(contract: dict, classifier: dict) -> tuple:
    """
    Check if L0 Fast Track eligibility criteria are met.
    
    Returns: (eligible: bool, reasons: list)
    - E1: risk_level = LOW
    - E2: out_of_scope defined
    - E3: only docs/config/CI files
    - E4: confidence >= 0.85
    - E5: conflict_status aligned or scorer_dominance
    - E6: NOT involve prod config
    - E7: NOT involve release-adjacent config
    """
    reasons = []
    eligible = True
    
    # E1: risk_level = LOW
    if contract.get("risk_level") != "LOW":
        eligible = False
        reasons.append("E1: risk_level is not LOW")
    
    # E2: out_of_scope defined
    out_of_scope = contract.get("scope_boundary", {}).get("out_of_scope", [])
    if not out_of_scope:
        eligible = False
        reasons.append("E2: out_of_scope not defined")
    
    # E3: only docs/config/CI files (check in_scope)
    in_scope = contract.get("scope_boundary", {}).get("in_scope", [])
    non_fast_track_patterns = ["src/", "src/services/", "src/processors/", "src/quality/"]
    involves_runtime = any(
        any(pattern in item for pattern in non_fast_track_patterns)
        for item in in_scope if isinstance(item, str)
    )
    if involves_runtime:
        eligible = False
        reasons.append("E3: involves src/ runtime logic")
    
    # E4: confidence >= 0.85
    if classifier.get("confidence", 0) < 0.85:
        eligible = False
        reasons.append("E4: confidence < 0.85")
    
    # E5: conflict_status aligned or scorer_dominance
    conflict_status = classifier.get("conflict_status", "")
    if conflict_status not in ["aligned", "scorer_dominance"]:
        eligible = False
        reasons.append("E5: conflict_status not aligned")
    
    # E6: NOT involve prod config - check in_scope for actual prod file paths
    # Only check in_scope (out_of_scope is what we're excluding, not including)
    prod_file_patterns = ["serverless.yml", "prod.config", "production.config", "prod.yml", "production.yml",
                          "/prod/", "/production/", "prod.json", "production.json"]
    in_scope_combined = " ".join(in_scope).lower()
    if any(p in in_scope_combined for p in prod_file_patterns):
        eligible = False
        reasons.append("E6: involves prod config files")
    
    # E7: NOT involve release-adjacent config
    release_patterns = ["release.json", "release_tag", "deploy-prod.yml", "deploy-dev.yml", ".github/workflows/deploy"]
    if any(p in in_scope_combined for p in release_patterns):
        eligible = False
        reasons.append("E7: involves release-adjacent config")
    
    return eligible, reasons


def lane_selector(execution_contract: dict, classifier_result: dict) -> dict:
    """
    Main lane selection logic following v0.4 spec.
    
    Implements:
    - L4 Releaser mandatory (no bypass)
    - L3 HIGH Risk with pre-approval
    - L2 Quick Fix vs Investigate split
    - L0 Fast Track with eligibility check
    - L1 Standard feature lane
    
    Returns dict with fields consumed by downstream:
      - lane/escalation_triggered/required_agents: pool.py, observability_report.py
      - qa_required/hitl_required/hitl_mode: pool.py, observability_report.py
      - l4_mandatory_delegation/bypass_risk: observability_report.py (governance signals)
      - escalation_reason: debug/audit
    """
    final_layer = classifier_result.get("final_layer", "")
    confidence = classifier_result.get("confidence", 0.0)
    conflict_status = classifier_result.get("conflict_status", "aligned")
    
    # Map layer names
    layer_map = {
        "L0_config_housekeeping": "L0",
        "L1_feature_dev": "L1",
        "L2_bug_fix": "L2",
        "L3_refactor": "L3",
        "L4_release": "L4",
        "L0": "L0",
        "L1": "L1",
        "L2": "L2",
        "L3": "L3",
        "L4": "L4",
    }
    
    layer = layer_map.get(final_layer, final_layer)
    risk_level = execution_contract.get("risk_level", "MEDIUM")
    residual_ambiguity = execution_contract.get("residual_ambiguity", [])
    
    # Step 1: L4 Check (highest priority) - Releaser mandatory
    if layer == "L4":
        return {
            "lane": LANE_L4_RELEASER,
            "escalation_triggered": True,
            "escalation_reason": "L4 release task - mandatory delegation to agent-releaser",
            "required_agents": [AGENT_RELEASER],
            "qa_required": True,
            "hitl_required": True,
            "hitl_mode": "pre_approval",
            "l4_mandatory_delegation": True,
            "bypass_risk": "ZERO_BYPASS — mandatory agent-releaser delegation and HITL pre-approval",
        }
    
    # Step 2: L3 Check - HIGH Risk with pre-approval
    if layer == "L3":
        return {
            "lane": LANE_L3_HIGH_RISK,
            "escalation_triggered": True,
            "escalation_reason": "L3 refactoring - HIGH risk requires pre-approval",
            "required_agents": ["Architect", "Developer", "QA"],
            "qa_required": True,
            "hitl_required": True,
            "hitl_mode": "pre_approval",
            "l4_mandatory_delegation": False,
            "bypass_risk": "HIGH — pre-approval required; no bypass allowed",
        }
    
    # Step 3: L2 Check - Quick Fix vs Investigate split
    if layer == "L2":
        # Quick Fix conditions (Q1-Q6 from spec)
        # Simplified: if confidence >= 0.85 and no residual ambiguity, likely Quick Fix
        # Full implementation would check root cause known, regression available, etc.
        if confidence >= 0.85 and not residual_ambiguity and risk_level == "MEDIUM":
            return {
                "lane": LANE_L2_QUICK_FIX,
                "escalation_triggered": False,
                "escalation_reason": None,
                "required_agents": ["Developer", "QA", "Architect (spot-check)"],
                "qa_required": True,
                "hitl_required": False,
                "hitl_mode": "review",
                "l4_mandatory_delegation": False,
                "bypass_risk": "MEDIUM — QA regression required; no debugger only when root cause is known",
            }
        else:
            # Investigate - root cause unknown or ambiguous
            return {
                "lane": LANE_L2_INVESTIGATE,
                "escalation_triggered": bool(residual_ambiguity),
                "escalation_reason": "Root cause unknown or ambiguous - Debugger path required" if residual_ambiguity else None,
                "required_agents": ["Debugger", "Developer", "QA", "Architect (spot-check)"],
                "qa_required": True,
                "hitl_required": False,
                "hitl_mode": "review",
                "l4_mandatory_delegation": False,
                "bypass_risk": "MEDIUM — debugger path required for ambiguity/root-cause risk",
            }
    
    # Step 4: L1 Check - Standard Feature Lane
    if layer == "L1":
        return {
            "lane": LANE_L1_STANDARD,
            "escalation_triggered": False,
            "escalation_reason": None,
            "required_agents": ["Architect", "Developer", "QA"],
            "qa_required": True,
            "hitl_required": False,
            "hitl_mode": "review",
            "l4_mandatory_delegation": False,
            "bypass_risk": "MEDIUM — feature workflow requires QA and Architect review",
        }
    
    # Step 5: L0 Check - Fast Track eligibility
    if layer == "L0":
        eligible, reasons = check_l0_fast_track_eligibility(execution_contract, classifier_result)
        
        if eligible:
            return {
                "lane": LANE_L0_FAST_TRACK,
                "escalation_triggered": False,
                "escalation_reason": None,
                "required_agents": ["Developer"],
                "qa_required": False,
                "hitl_required": False,
                "hitl_mode": "auto_approve",
                "l4_mandatory_delegation": False,
                "bypass_risk": "LOW — guarded by E1-E7; no QA only if eligible",
            }
        else:
            # Escalation - L0 but not Fast Track eligible
            escalation_reason = "; ".join(reasons[:2]) if reasons else "L0 Fast Track eligibility not met"
            return {
                "lane": LANE_L1_STANDARD,  # Escalate to L1
                "escalation_triggered": True,
                "escalation_reason": escalation_reason,
                "required_agents": ["Architect", "Developer", "QA"],
                "qa_required": True,
                "hitl_required": False,
                "hitl_mode": "review",
                "l4_mandatory_delegation": False,
                "bypass_risk": "MEDIUM — Fast Track eligibility failed; escalated to L1 Standard with QA review",
            }
    
    # Default fallback - L1 Standard
    return {
        "lane": LANE_L1_STANDARD,
        "escalation_triggered": False,
        "escalation_reason": "Default fallback - layer unclear",
        "required_agents": ["Developer"],
        "qa_required": True,
        "hitl_required": False,
        "hitl_mode": "review",
        "l4_mandatory_delegation": False,
        "bypass_risk": "MEDIUM — fallback lane requires QA review",
    }


def load_input(path: str) -> dict:
    """Load input from JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Lane Selector Policy Runner — Phase v2.1 Runtime MVP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Sample fixtures:
  L0_Fast_Track  - L0 config with Fast Track eligibility
  L2_QuickFix    - L2 bug with known root cause
  L2_Investigate - L2 bug with unknown root cause
  L4_RELEASE     - L4 release (mandatory releaser delegation)
  L1_Feature     - L1 feature development
  L3_Refactor    - L3 refactoring (pre-approval required)

Examples:
  python3 scripts/lane_select.py --sample L2_QuickFix
  python3 scripts/lane_select.py input.json
        """
    )
    
    parser.add_argument("input", nargs="?", help="JSON input file")
    parser.add_argument("--sample", "-s", choices=list(SAMPLE_FIXTURES.keys()), 
                        help="Use sample fixture")
    
    args = parser.parse_args()
    
    # Get input data
    if args.sample:
        data = SAMPLE_FIXTURES[args.sample]
        print(f"Using sample: {args.sample}")
        print(f"Description: {data['description']}")
    elif args.input:
        data = load_input(args.input)
    else:
        # Try reading from stdin
        stdin_data = sys.stdin.read().strip()
        if stdin_data:
            data = json.loads(stdin_data)
        else:
            print("Error: No input provided. Use --sample or provide JSON file.")
            parser.print_help()
            sys.exit(1)
    
    # Extract components
    execution_contract = data.get("execution_contract", {})
    classifier_result = data.get("classifier_result", {})
    
    # Validate required fields
    if not classifier_result.get("final_layer"):
        print("Error: classifier_result.final_layer is required")
        sys.exit(1)
    
    # Execute lane selection
    decision = lane_selector(execution_contract, classifier_result)
    
    # Output result
    print("\nLane Decision:")
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
