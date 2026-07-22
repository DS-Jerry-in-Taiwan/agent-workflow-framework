# Validate Gate Rules

> Formal validation gate rules for AWF governance loop. Ensures output quality and completeness before tasks advance through execution lanes.
> Last updated: 2026-07-22 (v3.8)

---

## 1. Purpose

The Validate Gate is the formal checkpoint between the Developer completing implementation and the task advancing to the next governance state. It is not a code review — it is a structured quality assurance review against the Execution Contract.

The gate exists at every Developer → QA transition within a lane.

---

## 2. Coverage Matrix

The Validate Gate reviews all 8 fields of the Execution Contract. Each field requires coverage across 5 test categories:

| Field | 🟢 Positive | 🔴 Negative | 📏 Range | 🎯 Correctness | 🔲 Boundary |
|---|---|---|---|---|---|
| `clarified_spec` | Non-empty string, meaningful description | Empty string, whitespace-only | — | Matches actual task intent | Single word, very long text, unicode |
| `scope_boundary.in_scope` | Non-empty list for defined scope | — | — | Files/modules are actually in scope | Empty list (raw input), max items |
| `scope_boundary.out_of_scope` | Non-empty list for defined scope | — | — | Excluded items are correct | Empty list (raw input) |
| `success_criteria` | Measurable criteria present | Fabricated/vague criteria | — | Criteria match actual deliverable | Empty list (raw input), 1 item |
| `validation_plan` | Validation steps present | No-op or circular validation | — | Plan actually validates criteria | Empty list, generic fallback, max steps |
| `risk_level` | Valid enum value | Invalid value | — | Risk matches actual risk of task | LOW/MEDIUM/HIGH boundary |
| `recommended_layer` | Valid enum value | Invalid value | — | Layer matches actual complexity | L0/L1/L2/L3/L4 boundary |
| `residual_ambiguity` | Items documented for raw input | — | — | Ambiguities match actual unknowns | Empty for structured input, max items |

### Test Category Definitions

- **🟢 Positive test**: Correct input produces correct output
- **🔴 Negative test**: Invalid/malformed input is rejected or handled
- **📏 Range test**: Values within acceptable ranges are accepted
- **🎯 Correctness test**: Output matches actual real-world state
- **🔲 Boundary test**: Edge cases (empty, max, min) behave correctly

---

## 3. Retry Rules

### 3.1 Retry Limit

- **Maximum retries**: 3
- After 3 failed validation attempts, the task escalates to the User
- `retry_count` increments on each Validate Gate failure
- `validate_history` accumulates all retry attempts with timestamps and failure reasons

### 3.2 Retry Behavior

| `retry_count` | Action |
|---|---|
| 0 | Initial attempt — full coverage review |
| 1 | Retry with feedback from first attempt |
| 2 | Second retry — escalation warning issued |
| 3 | Final retry — if fails, escalate to User |
| > 3 | Escalate to User immediately |

### 3.3 Retry Data Required

Each retry must carry full Validate History:
```json
{
  "validate_history": [
    {
      "retry_count": 0,
      "timestamp": "ISO 8601",
      "result": "fail",
      "findings": [
        {
          "field": "success_criteria",
          "expected": "Measurable criteria",
          "actual": "Empty list",
          "verdict": "fail"
        }
      ],
      "reviewer": "QA"
    }
  ]
}
```

---

## 4. QA Report Format

QA produces a structured report for each Validate Gate review:

```json
{
  "task_id": "string",
  "lane": "string",
  "execution_contract": { "..." },
  "verdict": "pass | fail | conditional_pass",
  "retry_count": 0,
  "findings": [
    {
      "field": "string",
      "test_category": "positive | negative | range | correctness | boundary",
      "expected": "string",
      "actual": "string",
      "verdict": "pass | fail",
      "recommendation": "string"
    }
  ],
  "summary": "string",
  "reviewer": "QA",
  "timestamp": "ISO 8601"
}
```

### Verdict Definitions

- **pass**: All required fields meet coverage criteria
- **fail**: One or more required fields fail validation; retry eligible
- **conditional_pass**: All required fields pass, but residual ambiguity items remain unresolved; task may advance with documented risks

### Required Report Fields

1. `task_id` — identifies the pool item
2. `lane` — current execution lane
3. `execution_contract` — the contract under review
4. `verdict` — pass / fail / conditional_pass
5. `findings` — per-field list with expected/actual/verdict
6. `summary` — human-readable summary
7. `reviewer` — always "QA"
8. `timestamp` — ISO 8601

---

## 5. Architect Prompt Validate Gate Rules (Reference)

This document formalizes the implicit Validate Gate rules from the Architect prompt:

- QA Validate review: `findings[]` with `expected` vs `actual`, `verdict`
- Coverage matrix: every output field has positive, negative, range, correctness, and boundary test coverage
- Retry: max 3, `retry_count >= 3` → escalate to User
- `validate_history` accumulated on each retry
- HITL escalation at configurable thresholds

Source: Architect Prompt § Validate Gate

---

## 6. References

- `docs/architecture/development_stage_framework.md` — AWF governance loop architecture
- `docs/schemas/execution_contract_schema.json` — Execution Contract boundary schema
- `docs/intake_layer/routing_map_v1.json` — L0-L4 routing and confidence thresholds
- `scripts/lane_select.py` — lane selection with QA gates
- `scripts/orchestrator.py` — Validate Gate integration in governance loop
