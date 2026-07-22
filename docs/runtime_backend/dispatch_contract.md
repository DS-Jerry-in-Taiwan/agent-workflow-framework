# Dispatch Contract

> Contract schema for dispatch payloads produced by the AWF governance loop and consumed by downstream agents (AgEnD Transport / OmO Execution).
> This document is a contract specification — no implementation code is generated here.

---

## 1. Purpose

The dispatch payload is the structured work item that AWF produces when it decides to send a task to a downstream agent. It packages the lane decision, execution contract, classifier result, and continuation policy into a single, typed document.

The dispatch payload is consumed by:
- **AgEnD Transport / Session Loop** — receives the payload and routes it to the appropriate agent
- **OmO Execution Loop** — receives the payload and begins implementation planning

AWF does not execute the task itself; it dispatches and governs.

---

## 2. Dispatch Payload Schema

```json
{
  "dispatch_id": "uuid-v4",
  "created_at": "ISO 8601 timestamp",
  "lane": "L0_Fast_Track | L1_Standard | L2_QuickFix | L2_Investigate | L3_HighRisk | L4_Releaser",
  "execution_contract": {
    "clarified_spec": "string",
    "scope_boundary": {
      "in_scope": ["string"],
      "out_of_scope": ["string"]
    },
    "success_criteria": ["string"],
    "validation_plan": ["string"],
    "risk_level": "LOW | MEDIUM | HIGH",
    "recommended_layer": "L0_config_housekeeping | L1_feature_dev | L2_bug_fix | L3_refactor | L4_release",
    "next_step": "string",
    "residual_ambiguity": ["string"]
  },
  "classifier_result": {
    "final_layer": "string",
    "confidence": 0.0,
    "conflict_status": "aligned | conflicting | ambiguous"
  },
  "lane_decision": {
    "lane": "string",
    "required_agents": ["string"],
    "qa_required": true,
    "hitl_required": false,
    "hitl_mode": "auto | review | pre-approval"
  },
  "continuation_policy": {
    "state": "auto_continue | report_only | ask_user | mandatory_handoff | blocked",
    "reason": "string",
    "next_agent": "string | null"
  },
  "dispatch_metadata": {
    "pool_item_id": "string",
    "retry_count": 0,
    "max_retry": 3,
    "validate_history": []
  }
}
```

---

## 3. Field Definitions

| Field | Type | Required | Description |
|---|---|---|---|
| `dispatch_id` | string (UUID) | Yes | Unique identifier for this dispatch |
| `created_at` | string (ISO 8601) | Yes | Timestamp when dispatch was created |
| `lane` | enum | Yes | Target execution lane |
| `execution_contract` | object | Yes | Full 8-field Execution Contract (see `docs/schemas/execution_contract_schema.json`) |
| `classifier_result` | object | Yes | Classification output: layer, confidence, conflict status |
| `lane_decision` | object | Yes | Lane selection output: required agents, QA/HITL flags |
| `continuation_policy` | object | Yes | Continuation signal: state, reason, next agent |
| `dispatch_metadata` | object | Yes | Pool item tracking: item ID, retry state, validate history |

### 3.1 classifier_result

| Field | Type | Description |
|---|---|---|
| `final_layer` | string | Recommended L0-L4 layer |
| `confidence` | float | 0.0–1.0 routing confidence score |
| `conflict_status` | enum | `aligned` (keywords match), `conflicting` (conflicting signals), `ambiguous` (insufficient signals) |

### 3.2 lane_decision

| Field | Type | Description |
|---|---|---|
| `lane` | enum | Selected execution lane |
| `required_agents` | list[string] | Ordered list of agents required for this lane |
| `qa_required` | bool | Whether QA Validate review is required |
| `hitl_required` | bool | Whether Human-In-The-Loop escalation is required |
| `hitl_mode` | enum | `auto` (auto-approved), `review` (spot-check), `pre-approval` (mandatory review) |

### 3.3 continuation_policy

| Field | Type | Description |
|---|---|---|
| `state` | enum | auto_continue, report_only, ask_user, mandatory_handoff, blocked |
| `reason` | string | Human-readable explanation of the continuation decision |
| `next_agent` | string \| null | Next agent to receive dispatch, or null if task is complete |

---

## 4. Lane → Agent Mapping

Each lane defines the required agent sequence:

| Lane | Required Agents | Notes |
|---|---|---|
| L0_Fast_Track | Developer | Lightweight; QA auto-approved unless prod config changed |
| L1_Standard | Architect → Developer → QA | Architect plans, Developer implements, QA validates |
| L2_QuickFix | Developer → QA | Developer fixes, QA validates |
| L2_Investigate | Debugger → Developer → QA | Debugger identifies root cause, Developer fixes, QA validates |
| L3_HighRisk | Architect → Developer → QA | Pre-approval required; Architect must review design before implementation |
| L4_Releaser | agent-releaser | **Mandatory delegation** — Architect must not execute |

Source: `scripts/lane_select.py` lane definitions + `docs/intake_layer/routing_map_v1.json` routing_agent field.

---

## 5. Continuation Signal Format

The continuation signal tells the dispatch layer what to do next:

| State | Meaning |
|---|---|
| `auto_continue` | Task is progressing normally; dispatch to next agent |
| `report_only` | Task is complete; produce observability report, no further dispatch |
| `ask_user` | Task requires human input before proceeding; block and prompt |
| `mandatory_handoff` | Task must be handed off to a different agent role (e.g., L4 Releaser) |
| `blocked` | Task is blocked by an external dependency or error; await resolution |

Source: `scripts/continuation_policy.py` state machine.

---

## 6. Lifecycle

```
AWF Governance Loop
    │
    ├─ intake_classify (L0-L4 layer + confidence)
    ├─ lane_select (execution lane + required agents)
    ├─ pool (persist / queue)
    ├─ orchestrator (build continuation context)
    ├─ continuation_policy (determine next action)
    └─► DISPATCH PAYLOAD  ──► AgEnD Transport ──► OmO Execution Loop
                                     │
                                     ▼
                            Await Agent Result
                                     │
                                     ▼
                           AWF Governance Loop (resume)
```

---

## 7. References

- `docs/schemas/execution_contract_schema.json` — Execution Contract boundary schema
- `scripts/lane_select.py` — lane selection logic
- `scripts/continuation_policy.py` — continuation state machine
- `scripts/orchestrator.py` — bridge between pool and continuation policy
- `docs/intake_layer/routing_map_v1.json` — L0-L4 routing
