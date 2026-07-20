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

import json
import os
import re
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Event observability (Stream B — Phase 1)
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = "v3.7-b1"

EVENT_TYPES: tuple[str, ...] = (
    "workflow.classified",
    "workflow.lane_selected",
    "workflow.governance_blocked",
    "workflow.dispatched",
    "workflow.validate_result",
    "workflow.runner_loop_iteration",
)

# Secret-like patterns that should be detected/redacted
_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)\b(api[_-]?key|apikey|api[_-]?secret)\b"),
    re.compile(r"(?i)\b(token|auth[_-]?token|access[_-]?token)\b"),
    re.compile(r"(?i)\b(password|passwd)\b"),
    re.compile(r"(?i)\bsecret\b"),
    re.compile(r"-----BEGIN\s+\w+\s+PRIVATE\s+KEY-----"),
    re.compile(r"(?i)\b(ghp_|gho_|ghu_|ghs_|ghr_)[a-zA-Z0-9]{36}\b"),
    re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"),
]

_EVENT_ID_PATTERN: re.Pattern = re.compile(r"^evt-[a-zA-Z0-9_-]+-\d{5}$")

# ---------------------------------------------------------------------------
# Event writer helpers
# ---------------------------------------------------------------------------


def _generate_session_id() -> str:
    """Generate a short stable session ID for event_id prefix."""
    # Use a hash of hostname + pid + random suffix for uniqueness
    base = f"{uuid.uuid4().hex[:8]}"
    return f"session-{base}"


def _detect_secrets(text: str) -> int:
    """Return count of secret-like patterns found in text."""
    return sum(1 for pat in _SECRET_PATTERNS if pat.search(text))


def _apply_summary_privacy(original_request: str | None) -> tuple[str, int, str]:
    """
    Apply summary-mode privacy to an original_request string.

    Returns (preview, secrets_count, redaction_reason).
    """
    if original_request is None:
        return "", 0, "default privacy mode: summary"

    secrets_count = _detect_secrets(original_request)
    if secrets_count > 0:
        return "[REDACTED - potential secret]", secrets_count, "secret-like patterns detected"

    if len(original_request) <= 80:
        return original_request, 0, "default privacy mode: summary"

    return original_request[:80] + "...", 0, "default privacy mode: summary"


def _build_privacy_block(
    mode: str,
    original_request: str | None,
    redaction_reason_override: str | None = None,
) -> dict:
    """Build the privacy sub-block for an event envelope."""
    if mode == "raw":
        secrets_count = _detect_secrets(original_request or "")
        preview = original_request or ""
        redacted = False
        reason: str | None = None
    else:
        preview, secrets_count, default_reason = _apply_summary_privacy(original_request)
        redacted = secrets_count > 0 or (original_request is not None and len(original_request) > 80)
        reason = redaction_reason_override or default_reason

    return {
        "mode": mode,
        "original_request_redacted": redacted,
        "original_request_preview": preview,
        "secrets_redacted_count": secrets_count,
        "redaction_reason": reason,
    }


def _default_events_dir() -> Path:
    """Return the events directory from env or default."""
    env_dir = os.environ.get("AGEND_EVENTS_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).parent.parent / "docs" / "agent_context" / "events"


def _iso_timestamp() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# WorkflowEventWriter
# ---------------------------------------------------------------------------


class WorkflowEventWriter:
    """
    File-first JSONL event writer for workflow observability.

    Best-effort writes: failure to write does not crash the caller.
    Events are appended to ``events-YYYY-MM-DD.jsonl`` in the events directory.

    Privacy default is "summary" — raw original_request requires AGEND_EVENT_PRIVACY=raw.
    """

    def __init__(
        self,
        events_dir: Path | str | None = None,
        max_buffer: int = 100,
        privacy_mode: str | None = None,
    ):
        self.events_dir: Path = Path(events_dir) if events_dir else _default_events_dir()
        self.max_buffer: int = max_buffer
        self._default_privacy_mode: str = privacy_mode or os.environ.get(
            "AGEND_EVENT_PRIVACY", "summary"
        )
        self._session_id: str = _generate_session_id()
        self._seq: int = 0
        self._buffer: deque[str] = deque(maxlen=max_buffer)
        self._current_file_date: str | None = None
        self._file_handle: Any = None  # opened lazily
        # Ensure directory exists
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # best-effort; write will fail gracefully

    def _current_file(self) -> Path:
        """Return today's event file path, rotating if the date changed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_file_date:
            self._close_file()
            self._current_file_date = today
        return self.events_dir / f"events-{today}.jsonl"

    def _open_file(self) -> Any:
        """Lazily open today's file for append."""
        if self._file_handle is None:
            try:
                self._file_handle = open(self._current_file(), "a", encoding="utf-8")
            except OSError as e:
                print(f"EVENT_WRITE_FAILED: cannot open {self._current_file()}: {e}", file=sys.stderr)
                return None
        return self._file_handle

    def _close_file(self) -> None:
        """Close the current file handle."""
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except OSError:
                pass
            self._file_handle = None

    def _write_line(self, line: str) -> bool:
        """
        Append one JSON line to the daily event file.

        Returns True if write succeeded, False otherwise.
        On failure: appends to in-memory buffer; prints warning to stderr.
        """
        fh = self._open_file()
        if fh is None:
            self._buffer.append(line)
            if len(self._buffer) >= self.max_buffer:
                dropped = self._buffer.popleft()
                print(
                    f"EVENT_BUFFER_FULL: dropping oldest event (buffer size {self.max_buffer})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"EVENT_WRITE_FAILED: buffered (unwritable directory); buffer size {len(self._buffer)}",
                    file=sys.stderr,
                )
            return False

        try:
            fh.write(line + "\n")
            fh.flush()
            # Replay buffered events
            while self._buffer:
                buffered = self._buffer.popleft()
                try:
                    fh.write(buffered + "\n")
                except OSError:
                    self._buffer.appendleft(buffered)  # put it back
                    break
            fh.flush()
            return True
        except OSError as e:
            print(f"EVENT_WRITE_FAILED: {e}", file=sys.stderr)
            self._buffer.append(line)
            if len(self._buffer) >= self.max_buffer:
                self._buffer.popleft()
                print(
                    f"EVENT_BUFFER_FULL: dropping oldest event (buffer size {self.max_buffer})",
                    file=sys.stderr,
                )
            return False

    def emit_event(
        self,
        event_type: str,
        data: dict,
        workflow_id: str | None = None,
        correlation_id: str | None = None,
        privacy_mode: str | None = None,
        original_request: str | None = None,
    ) -> str:
        """
        Build, write (or buffer) an event.

        Returns the event_id string. Never raises; logs to stderr on failure.
        """
        self._seq += 1
        event_id = f"evt-{self._session_id}-{self._seq:05d}"
        mode = privacy_mode or self._default_privacy_mode

        event = {
            "event_id": event_id,
            "event_type": event_type,
            "timestamp": _iso_timestamp(),
            "source": "agend_message_adapter.emit_event",
            "workflow_id": workflow_id,
            "correlation_id": correlation_id or workflow_id,
            "data": data,
            "privacy": _build_privacy_block(mode, original_request),
            "schema_version": SCHEMA_VERSION,
        }

        try:
            line = json.dumps(event, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            print(f"EVENT_SERIALIZE_FAILED: {e}", file=sys.stderr)
            return event_id

        self._write_line(line)
        return event_id

    def flush(self) -> int:
        """Flush buffered events to disk. Returns count of events flushed."""
        if not self._buffer:
            return 0
        flushed = 0
        while self._buffer:
            line = self._buffer.popleft()
            if self._write_line(line):
                flushed += 1
            else:
                break  # write failed again; stop flushing
        return flushed

    def close(self) -> None:
        """Flush and close resources."""
        self.flush()
        self._close_file()

    def __enter__(self) -> "WorkflowEventWriter":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Event validation helpers
# ---------------------------------------------------------------------------


def validate_event_line(line: str) -> tuple[bool, list[str]]:
    """
    Validate a single JSONL event line.

    Returns (True, []) if valid, (False, [error, ...]) otherwise.

    Checks:
    - V1: well-formed JSON
    - V2: envelope completeness (all 9 fields)
    - V3: event_type is a registered type
    - V4: timestamp is ISO 8601 with Z suffix
    - V5: event_id matches pattern
    - V6: schema_version == "v3.7-b1"
    - V7: privacy block has all 5 sub-fields
    - V9: no trailing commas (strict JSON)
    - V10: valid UTF-8
    """
    errors: list[str] = []
    stripped = line.strip()

    if not stripped:
        return False, ["empty line"]

    # V1: parse JSON
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError as e:
        return False, [f"invalid JSON: {e}"]

    if not isinstance(event, dict):
        return False, ["event is not a JSON object"]

    # V2: all 9 envelope fields
    required_envelope = [
        "event_id", "event_type", "timestamp", "source",
        "workflow_id", "correlation_id", "data", "privacy", "schema_version",
    ]
    for field in required_envelope:
        if field not in event:
            errors.append(f"missing envelope field: {field}")

    if errors:
        return False, errors

    # V3: event_type
    if event["event_type"] not in EVENT_TYPES:
        errors.append(f"event_type '{event['event_type']}' is not registered")

    # V4: timestamp ISO 8601 with Z
    ts = event["timestamp"]
    if not isinstance(ts, str) or not ts.endswith("Z"):
        errors.append(f"timestamp '{ts}' must be ISO 8601 with Z suffix")
    else:
        try:
            # Remove Z and parse
            datetime.fromisoformat(ts[:-1] + "+00:00")
        except ValueError:
            errors.append(f"timestamp '{ts}' is not valid ISO 8601")

    # V5: event_id pattern
    eid = event["event_id"]
    if not isinstance(eid, str) or not _EVENT_ID_PATTERN.match(eid):
        errors.append(f"event_id '{eid}' does not match pattern evt-[session]-[seq:05d]")

    # V6: schema_version
    if event["schema_version"] != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be '{SCHEMA_VERSION}', got '{event['schema_version']}'"
        )

    # V7: privacy block
    privacy = event.get("privacy")
    if isinstance(privacy, dict):
        required_privacy = [
            "mode", "original_request_redacted", "original_request_preview",
            "secrets_redacted_count", "redaction_reason",
        ]
        for pf in required_privacy:
            if pf not in privacy:
                errors.append(f"privacy missing sub-field: {pf}")
    else:
        errors.append("privacy block is missing or not a dict")

    return len(errors) == 0, errors


def validate_event_file(filepath: str | Path) -> dict:
    """
    Validate an entire event file.

    Returns:
        {
            "filepath": str,
            "total_lines": int,
            "valid_lines": int,
            "invalid_lines": int,
            "errors": [{"line": int, "messages": [str]}],
            "event_type_counts": {str: int},
            "first_timestamp": str | None,
            "last_timestamp": str | None,
        }
    """
    filepath = Path(filepath)
    result: dict = {
        "filepath": str(filepath),
        "total_lines": 0,
        "valid_lines": 0,
        "invalid_lines": 0,
        "errors": [],
        "event_type_counts": {},
        "first_timestamp": None,
        "last_timestamp": None,
    }

    if not filepath.exists():
        return result

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                result["total_lines"] += 1
                valid, errs = validate_event_line(line)
                if valid:
                    result["valid_lines"] += 1
                    try:
                        obj = json.loads(line.strip())
                        et = obj.get("event_type", "unknown")
                        result["event_type_counts"][et] = result["event_type_counts"].get(et, 0) + 1
                        ts = obj.get("timestamp")
                        if ts and result["first_timestamp"] is None:
                            result["first_timestamp"] = ts
                        result["last_timestamp"] = ts
                    except Exception:
                        pass
                else:
                    result["invalid_lines"] += 1
                    result["errors"].append({"line": line_no, "messages": errs})
    except OSError as e:
        result["errors"].append({"line": 0, "messages": [f"cannot read file: {e}"]})

    return result


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
