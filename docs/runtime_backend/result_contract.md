# Result Contract

> Contract schema for agent result payloads returned to the AWF governance loop after execution completes.
> This document is a contract specification — no implementation code is generated here.

---

## 1. Purpose

The result payload is the structured response that an agent (Developer, QA, Debugger, Architect, agent-releaser) returns to the AWF governance loop after completing its work. It signals task completion, failure, or the need for human intervention.

The result payload is consumed by:
- **AWF Governance Loop** — reads the status to determine the next continuation state
- **Orchestrator** — updates pool item status based on result
- **Observability Report** — records task outcomes for governance metrics

AWF does not execute tasks — it dispatches and governs based on agent results.

---

## 2. Result Payload Schema

```json
{
  "result_id": "uuid-v4",
  "dispatch_id": "uuid-v4",
  "status": "completed | failed | hitl_request | blocked",
  "agent": "string",
  "timestamp": "ISO 8601",
  "summary": "string",
  "artifacts": ["string"],
  "errors": ["string"],
  "metadata": {
    "execution_duration_seconds": 0,
    "retry_count": 0,
    "exit_code": 0
  }
}
```

---

## 3. Field Definitions

| Field | Type | Required | Description |
|---|---|---|---|
| `result_id` | string (UUID) | Yes | Unique identifier for this result |
| `dispatch_id` | string (UUID) | Yes | ID of the dispatch that triggered this execution |
| `status` | enum | **Yes** | Task outcome: completed, failed, hitl_request, blocked |
| `agent` | string | **Yes** | Which agent produced this result (Developer, QA, Debugger, etc.) |
| `timestamp` | string (ISO 8601) | **Yes** | When the result was produced |
| `summary` | string | Yes | Human-readable summary of what was done and the outcome |
| `artifacts` | list[string] | No | File paths, PR URLs, commit SHAs, or other produced artifacts |
| `errors` | list[string] | No | Error messages or failure reasons (only meaningful when status is failed or blocked) |
| `metadata` | object | No | Execution metadata: duration, retry count, exit code |

### 3.1 status

| Value | Meaning |
|---|---|
| `completed` | Agent finished successfully; task meets success criteria |
| `failed` | Agent encountered an error; task could not be completed |
| `hitl_request` | Agent needs human input or approval before proceeding |
| `blocked` | Task is blocked by an external dependency (awaiting upstream, infra, etc.) |

### 3.2 metadata

| Field | Type | Description |
|---|---|---|
| `execution_duration_seconds` | int | How long the agent spent on this task |
| `retry_count` | int | Current retry count for this task |
| `exit_code` | int | Agent process exit code (0 = success, non-zero = failure) |

---

## 4. Validation Rules

Result contracts must pass these validation rules before being consumed by the AWF governance loop:

### 4.1 Required Field Rules

| Field | Rule |
|---|---|
| `status` | Must be one of: `completed`, `failed`, `hitl_request`, `blocked` |
| `agent` | Must be a non-empty string |
| `timestamp` | Must be a valid ISO 8601 timestamp |
| `summary` | Must be a non-empty string |
| `result_id` | Must be a valid UUID |
| `dispatch_id` | Must match a known dispatch_id from the pool |

### 4.2 Status-Specific Rules

| Status | Required Fields | Forbidden Fields |
|---|---|---|
| `completed` | `artifacts` (can be empty list) | `errors` should be empty or null |
| `failed` | `errors` (must be non-empty) | — |
| `hitl_request` | `errors` (reason for HITL request) | — |
| `blocked` | `errors` (reason for block) | — |

### 4.3 Timestamp Validation

The `timestamp` field must be parseable as ISO 8601:
- Full format: `2026-07-22T14:30:00Z`
- With timezone: `2026-07-22T14:30:00+08:00`
- With milliseconds: `2026-07-22T14:30:00.123Z`

---

## 5. Error Codes

Agents return structured error codes to aid in diagnostic and governance:

| Error Code | Meaning |
|---|---|
| `ERR_UNKNOWN_STATUS` | Result `status` field contains an unrecognized value |
| `ERR_MISSING_AGENT` | `agent` field is empty or missing |
| `ERR_MISSING_TIMESTAMP` | `timestamp` field is missing or not ISO 8601 parseable |
| `ERR_MISSING_DISPATCH_ID` | `dispatch_id` field is missing or does not match a known dispatch |
| `ERR_MISSING_SUMMARY` | `summary` field is empty |
| `ERR_INVALID_ARTIFACT` | One or more artifact paths are invalid or inaccessible |
| `ERR_INTERNAL` | Unexpected internal error in the agent or execution environment |
| `ERR_TIMEOUT` | Agent execution exceeded the configured timeout |
| `ERR_PERMISSION_DENIED` | Agent lacks permissions to access required resources |

---

## 6. Lifecycle

```
AWF Governance Loop
    │
    └─► DISPATCH PAYLOAD  ──► Agent (Developer / QA / Debugger / etc.)
                                        │
                                        ▼
                              AGENT EXECUTION
                                        │
                                        ▼
                              RESULT PAYLOAD
                                        │
                                        ▼
                          AWF Governance Loop (resume)
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
               completed            hitl_request          failed
                    │                   │                   │
            next agent /          Await human input     increment retry
            close task               (blocked)             │
                                                            ▼
                                                    retry < max → retry
                                                    retry >= max → escalate to User
```

---

## 7. References

- `docs/schemas/execution_contract_schema.json` — Execution Contract boundary schema
- `docs/runtime_backend/dispatch_contract.md` — Dispatch payload schema (inverse of this)
- `scripts/continuation_policy.py` — Continuation state machine that consumes result status
- `scripts/orchestrator.py` — Pool item update logic based on result
- `scripts/observability_report.py` — Records task outcomes for governance metrics
