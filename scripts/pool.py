#!/usr/bin/env python3
"""
Task Pool CLI — Phase v2.1 Runtime MVP

File-based task pool management for cross-session task queuing.
Uses docs/agent_context/pool/ directory structure.
Supports commands: init, add, list, pick, status, complete

Usage:
    python3 scripts/pool.py init
    python3 scripts/pool.py add --title "Fix threshold bug" --layer L2
    python3 scripts/pool.py list
    python3 scripts/pool.py list --status pending
    python3 scripts/pool.py pick
    python3 scripts/pool.py status pool-20260716-001
    python3 scripts/pool.py complete pool-20260716-001

Pool Directory Structure:
    docs/agent_context/pool/
    ├── active/           # Currently processing items
    ├── pending/          # Queued items
    ├── blocked/          # Items waiting on dependencies
    ├── completed/         # Finished items
    └── pool.yaml          # Global pool state index

Schema follows v1.0 Task Pool spec with safe defaults:
    - retry_count: 0
    - max_retry: 3
    - validate_history: []
    - depends_on: []
    - blocked_by: []

Data contract — item dict fields consumed by observability_report.py:
    - status, id: index loop, status counts
    - execution_contract.recommended_layer: layer counts (primary)
    - execution_contract.risk_level: risk counts
    - classifier_result.final_layer: layer counts (fallback)
    - lane_decision.lane: lane counts
    - lane_decision.l4_mandatory_delegation: governance signals
    - lane_decision.hitl_required/qa_required/hitl_mode: governance signals
    - lane_decision.escalation_triggered: escalated state
    - retry_count, max_retry, validate_history: retry/validation summary
    - is_pilot, artifact_type: pilot counts
    When adding any new field to the item schema, update observability_report.py accordingly.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Pool directory path
POOL_ROOT = Path(__file__).parent.parent / "docs" / "agent_context" / "pool"
POOL_INDEX = POOL_ROOT / "pool.yaml"
SUBDIRS = ["active", "pending", "blocked", "completed"]

# Status enum (from v1.0 spec)
STATUS_PENDING = "pending"
STATUS_PICKED = "picked"
STATUS_IN_PROGRESS = "in_progress"
STATUS_QA_PENDING = "qa_pending"
STATUS_VALIDATED = "validated"
STATUS_BLOCKED = "blocked"
STATUS_CANCELLED = "cancelled"
STATUS_ESCALATED = "escalated"
STATUS_HELD = "held"
STATUS_COMPLETED = "completed"

# Valid statuses
VALID_STATUSES = [
    STATUS_PENDING, STATUS_PICKED, STATUS_IN_PROGRESS, STATUS_QA_PENDING,
    STATUS_VALIDATED, STATUS_BLOCKED, STATUS_CANCELLED, STATUS_ESCALATED,
    STATUS_HELD, STATUS_COMPLETED
]

# Lanes (from v0.4 spec)
VALID_LANES = [
    "L0_Fast_Track", "L1_Standard", "L2_QuickFix", "L2_Investigate",
    "L3_HighRisk", "L4_Releaser"
]

# Layers (from routing_map_v1.json)
VALID_LAYERS = [
    "L0_config_housekeeping", "L1_feature_dev", "L2_bug_fix",
    "L3_refactor", "L4_release"
]


def ensure_pool_dirs():
    """Ensure all pool subdirectories exist."""
    POOL_ROOT.mkdir(parents=True, exist_ok=True)
    for subdir in SUBDIRS:
        (POOL_ROOT / subdir).mkdir(exist_ok=True)


def load_pool_index() -> dict:
    """Load pool.yaml (JSON-compatible YAML) as global state index."""
    if not POOL_INDEX.exists():
        return {"items": [], "version": "v1.0", "updated_at": None}
    
    with open(POOL_INDEX, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {"items": [], "version": "v1.0", "updated_at": None}
        # JSON-compatible parsing (stdlib json for simplicity)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Fallback for empty or invalid content
            return {"items": [], "version": "v1.0", "updated_at": None}


def save_pool_index(pool_index: dict):
    """Save pool.yaml with JSON content (JSON-compatible YAML)."""
    pool_index["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(POOL_INDEX, "w", encoding="utf-8") as f:
        json.dump(pool_index, f, indent=2, ensure_ascii=False)


def generate_id() -> str:
    """Generate unique pool item ID: pool-YYYYMMDD-NNN"""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    
    # Count existing items for sequence number
    pool_index = load_pool_index()
    existing = [item for item in pool_index.get("items", []) if item["id"].startswith(f"pool-{date_str}")]
    seq = len(existing) + 1
    
    return f"pool-{date_str}-{seq:03d}"


def create_item_file(item_id: str, item_data: dict, status: str, remove_from_other: bool = False):
    """
    Create item YAML file in appropriate subdirectory.
    
    If remove_from_other is True, removes the item file from other directories.
    """
    subdir_map = {
        STATUS_PENDING: "pending",
        STATUS_PICKED: "active",
        STATUS_IN_PROGRESS: "active",
        STATUS_QA_PENDING: "pending",
        STATUS_VALIDATED: "pending",
        STATUS_BLOCKED: "blocked",
        STATUS_CANCELLED: "completed",
        STATUS_ESCALATED: "blocked",
        STATUS_HELD: "pending",
        STATUS_COMPLETED: "completed",
    }
    
    subdir = subdir_map.get(status, "pending")
    filepath = POOL_ROOT / subdir / f"{item_id}.json"
    
    # Remove from other directories if requested
    if remove_from_other:
        for other_dir in SUBDIRS:
            other_file = POOL_ROOT / other_dir / f"{item_id}.json"
            if other_file.exists() and other_file != filepath:
                other_file.unlink()
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(item_data, f, indent=2, ensure_ascii=False)
    
    return filepath


def load_item_file(item_id: str) -> Optional[dict]:
    """Load item from any pool subdirectory."""
    for subdir in SUBDIRS:
        filepath = POOL_ROOT / subdir / f"{item_id}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    return None


def move_item_file(item_id: str, from_status: str, to_status: str):
    """Move item file between subdirectories on status change."""
    from_subdir_map = {
        STATUS_PENDING: "pending",
        STATUS_PICKED: "active",
        STATUS_IN_PROGRESS: "active",
        STATUS_QA_PENDING: "pending",
        STATUS_VALIDATED: "pending",
        STATUS_BLOCKED: "blocked",
        STATUS_CANCELLED: "completed",
        STATUS_ESCALATED: "blocked",
        STATUS_HELD: "pending",
        STATUS_COMPLETED: "completed",
    }
    
    to_subdir_map = from_subdir_map.copy()
    
    from_subdir = from_subdir_map.get(from_status, "pending")
    to_subdir = to_subdir_map.get(to_status, "pending")
    
    if from_subdir == to_subdir:
        return  # Same directory, no move needed
    
    src = POOL_ROOT / from_subdir / f"{item_id}.json"
    dst = POOL_ROOT / to_subdir / f"{item_id}.json"
    
    if src.exists():
        data = json.loads(src.read_text())
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        src.unlink()


def cmd_init():
    """Initialize pool directory structure and pool.yaml."""
    ensure_pool_dirs()
    
    pool_index = load_pool_index()
    if pool_index.get("items"):
        print(f"Pool already initialized at {POOL_ROOT}")
        print(f"Existing items: {len(pool_index['items'])}")
        return
    
    pool_index = {
        "version": "v1.0",
        "items": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    save_pool_index(pool_index)
    print(f"Pool initialized at {POOL_ROOT}")
    print(f"Created directories: {', '.join(SUBDIRS)}")
    print(f"Created pool.yaml index.")


def cmd_add(args):
    """Add a new task to the pool."""
    ensure_pool_dirs()

    # --- File-based add path ---
    if args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error: Failed to parse JSON from {args.file}: {e}", file=sys.stderr)
            sys.exit(1)

        # Determine ID: use existing or generate new
        item_id = loaded.get("id") or generate_id()
        now = datetime.now(timezone.utc).isoformat()

        # Determine pilot metadata: CLI --pilot takes precedence over loaded value
        is_pilot = bool(args.pilot or loaded.get("is_pilot", False))
        artifact_type = "pilot" if is_pilot else loaded.get("artifact_type", "task")

        # Build item data: start with loaded, apply safe defaults for missing fields
        item_data = {
            # Core required fields
            "id": item_id,
            "title": loaded.get("title", f"Task from {filepath.name}"),
            "created_at": loaded.get("created_at") or now,
            "updated_at": now,
            "status": loaded.get("status") or STATUS_PENDING,
            # execution_contract
            "execution_contract": loaded.get("execution_contract") or {
                "clarified_spec": loaded.get("title", f"Task from {filepath.name}"),
                "scope_boundary": {"in_scope": [], "out_of_scope": []},
                "success_criteria": [],
                "validation_plan": [],
                "risk_level": loaded.get("risk_level", "MEDIUM"),
                "recommended_layer": loaded.get("layer") or "L1_feature_dev",
                "next_step": "pending",
                "residual_ambiguity": [],
            },
            # classifier_result
            "classifier_result": loaded.get("classifier_result") or {
                "final_layer": loaded.get("layer") or "L1_feature_dev",
                "confidence": 0.0,
                "conflict_status": "aligned",
            },
            # lane_decision
            "lane_decision": loaded.get("lane_decision") or {
                "lane": loaded.get("lane") or "L1_Standard",
                "escalation_triggered": False,
                "escalation_reason": None,
                "required_agents": ["Developer"],
                "qa_required": True,
                "hitl_required": False,
                "hitl_mode": "review",
            },
            # v1.0 safe defaults
            "retry_count": loaded.get("retry_count", 0),
            "max_retry": loaded.get("max_retry", 3),
            "validate_history": loaded.get("validate_history", []),
            "hitl_state": loaded.get("hitl_state", "not_required"),
            "hitl_approval_ref": loaded.get("hitl_approval_ref"),
            "depends_on": loaded.get("depends_on", []),
            "blocked_by": loaded.get("blocked_by", []),
            "owner": loaded.get("owner"),
            "session_ref": loaded.get("session_ref"),
            "audit_log_ref": loaded.get("audit_log_ref") or f"pending/{item_id}_audit.md",
            "priority": loaded.get("priority", 999),
            # pilot artifact metadata
            "is_pilot": is_pilot,
            "artifact_type": artifact_type,
        }

        # Write item file to pool directory
        create_item_file(item_id, item_data, STATUS_PENDING, remove_from_other=False)

        # Synchronize pool.yaml
        pool_index = load_pool_index()
        layer = item_data["execution_contract"].get("recommended_layer", "L1_feature_dev")
        # Avoid duplicates if item with same ID was already in pool
        existing = [i for i in pool_index["items"] if i["id"] == item_id]
        if not existing:
            pool_index["items"].append({
                "id": item_id,
                "status": STATUS_PENDING,
                "title": item_data["title"],
                "layer": layer,
                "priority": item_data.get("priority", 999),
                "created_at": item_data["created_at"],
                "is_pilot": is_pilot,
                "artifact_type": artifact_type,
            })
        else:
            # Update existing entry
            for entry in pool_index["items"]:
                if entry["id"] == item_id:
                    entry.update({
                        "status": STATUS_PENDING,
                        "title": item_data["title"],
                        "layer": layer,
                        "priority": item_data.get("priority", 999),
                        "is_pilot": is_pilot,
                        "artifact_type": artifact_type,
                    })
                    break
        save_pool_index(pool_index)

        print(f"Added task from file: {item_id}")
        print(f"  Title: {item_data['title']}")
        print(f"  Layer: {layer}")
        print(f"  Status: {STATUS_PENDING}")
        return

    # --- Title-based add path (original) ---
    if not args.title:
        print("Error: --title is required when --file is not provided.", file=sys.stderr)
        sys.exit(1)

    item_id = generate_id()
    now = datetime.now(timezone.utc).isoformat()

    # Build item data following v1.0 schema with safe defaults
    item_data = {
        "id": item_id,
        "title": args.title,
        "created_at": now,
        "updated_at": now,
        "status": STATUS_PENDING,
        "execution_contract": {
            "clarified_spec": args.title,
            "scope_boundary": {"in_scope": [], "out_of_scope": []},
            "success_criteria": [],
            "validation_plan": [],
            "risk_level": args.risk or "MEDIUM",
            "recommended_layer": args.layer or "L1_feature_dev",
            "next_step": "pending",
            "residual_ambiguity": [],
        },
        "classifier_result": {
            "final_layer": args.layer or "L1_feature_dev",
            "confidence": 0.0,
            "conflict_status": "aligned",
        },
        "lane_decision": {
            "lane": args.lane or "L1_Standard",
            "escalation_triggered": False,
            "escalation_reason": None,
            "required_agents": ["Developer"],
            "qa_required": True,
            "hitl_required": False,
            "hitl_mode": "review",
        },
        # v1.0 safe defaults
        "retry_count": 0,
        "max_retry": 3,
        "validate_history": [],
        "hitl_state": "not_required",
        "hitl_approval_ref": None,
        "depends_on": [],
        "blocked_by": [],
        "owner": None,
        "session_ref": None,
        "audit_log_ref": f"pending/{item_id}_audit.md",
        "priority": args.priority if args.priority is not None else 999,
        # pilot artifact metadata
        "is_pilot": bool(args.pilot),
        "artifact_type": "pilot" if args.pilot else "task",
    }

    # Create item file
    create_item_file(item_id, item_data, STATUS_PENDING, remove_from_other=False)

    # Update pool index
    pool_index = load_pool_index()
    pool_index["items"].append({
        "id": item_id,
        "status": STATUS_PENDING,
        "title": args.title,
        "layer": args.layer or "L1_feature_dev",
        "priority": args.priority if args.priority is not None else 999,
        "created_at": now,
        "is_pilot": bool(args.pilot),
        "artifact_type": "pilot" if args.pilot else "task",
    })
    save_pool_index(pool_index)

    print(f"Added task: {item_id}")
    print(f"  Title: {args.title}")
    print(f"  Layer: {args.layer or 'L1_feature_dev'}")
    print(f"  Status: {STATUS_PENDING}")


def cmd_list(args):
    """List pool items, optionally filtered by status."""
    pool_index = load_pool_index()
    items = pool_index.get("items", [])
    
    if not items:
        print("Pool is empty. Run 'pool.py init' first.")
        return
    
    # Filter by status if specified
    if args.status:
        items = [item for item in items if item.get("status") == args.status]
    
    if not items:
        print(f"No items with status '{args.status}'")
        return
    
    # Sort by created_at
    items.sort(key=lambda x: x.get("created_at", ""))
    
    print(f"Pool items ({len(items)} total):")
    for item in items:
        print(f"  [{item.get('status', 'unknown'):<15}] {item['id']}: {item.get('title', 'N/A')}")
        if item.get("layer"):
            print(f"      Layer: {item['layer']}")


def cmd_pick(args):
    """Pick the next available item from pending queue."""
    pool_index = load_pool_index()
    items = pool_index.get("items", [])
    
    # Find pending items not blocked
    pending = [
        item for item in items
        if item.get("status") == STATUS_PENDING
    ]
    
    if not pending:
        print("No pending items available.")
        return
    
    # Sort by priority (lower number = higher priority), then by created_at
    pending.sort(key=lambda x: (x.get("priority") or 999, x.get("created_at", "")))
    
    # Pick first non-blocked item
    picked = None
    for item in pending:
        item_data = load_item_file(item["id"])
        if item_data and not item_data.get("blocked_by"):
            picked = item
            break
    
    if not picked:
        print("All pending items are blocked by dependencies.")
        return
    
    item_id = picked["id"]
    
    # Update status
    picked["status"] = STATUS_PICKED
    
    # Update item file
    item_data = load_item_file(item_id)
    if item_data:
        item_data["status"] = STATUS_PICKED
        item_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        create_item_file(item_id, item_data, STATUS_PICKED, remove_from_other=True)
    
    save_pool_index(pool_index)
    
    print(f"Picked task: {item_id}")
    print(f"  Title: {picked.get('title', 'N/A')}")
    print(f"  Status: {STATUS_PICKED}")
    print(f"  Run 'pool.py status {item_id}' for details.")


def cmd_status(args):
    """Show detailed status of a pool item."""
    item_id = args.task_id
    item_data = load_item_file(item_id)
    
    if not item_data:
        print(f"Task not found: {item_id}")
        sys.exit(1)
    
    print(f"Task: {item_id}")
    print(f"  Title: {item_data.get('title', 'N/A')}")
    print(f"  Status: {item_data.get('status', 'unknown')}")
    print(f"  Layer: {item_data.get('execution_contract', {}).get('recommended_layer', 'N/A')}")
    print(f"  Lane: {item_data.get('lane_decision', {}).get('lane', 'N/A')}")
    print(f"  Created: {item_data.get('created_at', 'N/A')}")
    print(f"  Updated: {item_data.get('updated_at', 'N/A')}")
    print(f"  Retry: {item_data.get('retry_count', 0)}/{item_data.get('max_retry', 3)}")
    print(f"  Depends on: {item_data.get('depends_on', []) or 'None'}")
    print(f"  Blocked by: {item_data.get('blocked_by', []) or 'None'}")
    print(f"  Owner: {item_data.get('owner', 'Unclaimed')}")
    
    validate_history = item_data.get("validate_history", [])
    if validate_history:
        print(f"  Validation history:")
        for v in validate_history:
            print(f"    - Attempt {v.get('attempt')}: {v.get('result')} by {v.get('validator')}")


def cmd_complete(args):
    """Mark a task as completed."""
    item_id = args.task_id
    item_data = load_item_file(item_id)
    
    if not item_data:
        print(f"Task not found: {item_id}")
        sys.exit(1)
    
    current_status = item_data.get("status")
    
    # Validate transition
    if current_status not in [STATUS_VALIDATED, STATUS_IN_PROGRESS, STATUS_PICKED, STATUS_QA_PENDING]:
        print(f"Cannot complete from status '{current_status}'")
        print(f"Valid transitions to 'completed': validated, in_progress, qa_pending, picked")
        sys.exit(1)
    
    now = datetime.now(timezone.utc).isoformat()
    
    # Update item
    item_data["status"] = STATUS_COMPLETED
    item_data["updated_at"] = now
    item_data["completed_at"] = now
    
    # Update pool index
    pool_index = load_pool_index()
    for item in pool_index["items"]:
        if item["id"] == item_id:
            item["status"] = STATUS_COMPLETED
            break
    save_pool_index(pool_index)
    
    # Move file to completed directory (removes from other dirs)
    create_item_file(item_id, item_data, STATUS_COMPLETED, remove_from_other=True)
    
    print(f"Task completed: {item_id}")
    print(f"  Title: {item_data.get('title', 'N/A')}")
    print(f"  Final status: {STATUS_COMPLETED}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Task Pool CLI — Phase v2.1 Runtime MVP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  init                   Initialize pool directory structure
  add                    Add a new task to the pool
  list                   List all pool items
  pick                   Pick next available task
  status TASK_ID         Show task details
  complete TASK_ID       Mark task as completed

Examples:
  python3 scripts/pool.py init
  python3 scripts/pool.py add --title "Fix bug" --layer L2
  python3 scripts/pool.py list
  python3 scripts/pool.py list --status pending
  python3 scripts/pool.py pick
  python3 scripts/pool.py status pool-20260716-001
  python3 scripts/pool.py complete pool-20260716-001
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # init
    subparsers.add_parser("init", help="Initialize pool directory structure")
    
    # add
    add_parser = subparsers.add_parser("add", help="Add a new task")
    add_parser.add_argument("--title", "-t", help="Task title (required if --file not used)")
    add_parser.add_argument("--file", "-f", type=str, metavar="FILE",
                            help="Load task from FILE (JSON content; .yaml extension accepted but JSON parsing is used). "
                                 "If id is missing, one is auto-generated. "
                                 "Safe defaults are applied for missing fields.")
    add_parser.add_argument("--layer", "-l", choices=VALID_LAYERS, help="Task layer (L0-L4)")
    add_parser.add_argument("--lane", help="Task lane (L0_Fast_Track, L1_Standard, etc.)")
    add_parser.add_argument("--risk", choices=["LOW", "MEDIUM", "HIGH"], help="Risk level")
    add_parser.add_argument("--priority", "-p", type=int, help="Priority (1=highest)")
    add_parser.add_argument("--pilot", action="store_true", help="Mark task as generated pilot artifact")
    
    # list
    list_parser = subparsers.add_parser("list", help="List pool items")
    list_parser.add_argument("--status", "-s", choices=VALID_STATUSES, help="Filter by status")
    
    # pick
    subparsers.add_parser("pick", help="Pick next available task")
    
    # status
    status_parser = subparsers.add_parser("status", help="Show task details")
    status_parser.add_argument("task_id", help="Task ID (e.g., pool-20260716-001)")
    
    # complete
    complete_parser = subparsers.add_parser("complete", help="Mark task as completed")
    complete_parser.add_argument("task_id", help="Task ID (e.g., pool-20260716-001)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Execute command
    if args.command == "init":
        cmd_init()
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "pick":
        cmd_pick(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "complete":
        cmd_complete(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
