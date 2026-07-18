#!/usr/bin/env python3
"""
Unit tests for scripts/pool.py — Phase v2.4 Runtime Hardening.

Uses stdlib unittest only. Tests use tempfile.TemporaryDirectory() and
monkeypatch POOL_ROOT/POOL_INDEX to avoid touching real pool artifacts.

Covers:
- cmd_init() creates valid JSON-compatible pool.yaml
- cmd_add() title-based with pilot=True writes is_pilot:true / artifact_type:"pilot"
- cmd_add() title-based with pilot=False writes is_pilot:false / artifact_type:"task"
- cmd_add() file-based preserves/overrides pilot metadata
- Pool index entries include pilot metadata
- load_pool_index() reads JSON-compatible pool.yaml
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from argparse import Namespace

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import scripts.pool as pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(
    title=None,
    file=None,
    layer=None,
    lane=None,
    risk=None,
    priority=None,
    pilot=False,
):
    """Return a Namespace matching pool.py add parser arguments."""
    return Namespace(
        title=title,
        file=file,
        layer=layer,
        lane=lane,
        risk=risk,
        priority=priority,
        pilot=pilot,
    )


def _read_json(path):
    """Read and parse JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Tests — cmd_init()
# ---------------------------------------------------------------------------

class TestCmdInit(unittest.TestCase):
    """cmd_init() creates pool.yaml valid JSON-compatible content."""

    def test_init_creates_pool_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                self.assertTrue(pool_index.exists(), "pool.yaml was not created")

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                data = _read_json(pool_index)
                self.assertIn("version", data)
                self.assertIn("items", data)
                self.assertIsInstance(data["items"], list)

    def test_init_creates_subdirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                for subdir in pool.SUBDIRS:
                    subdir_path = pool_root / subdir
                    self.assertTrue(
                        subdir_path.exists(),
                        f"Subdirectory {subdir} was not created",
                    )


# ---------------------------------------------------------------------------
# Tests — cmd_add() title-based with pilot flag
# ---------------------------------------------------------------------------

class TestCmdAddPilotTrue(unittest.TestCase):
    """cmd_add() with pilot=True writes is_pilot:true and artifact_type:"pilot"."""

    def test_title_add_pilot_writes_is_pilot_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                args = _make_args(
                    title="Pilot artifact task",
                    layer="L0_config_housekeeping",
                    lane="L0_Fast_Track",
                    risk="LOW",
                    priority=1,
                    pilot=True,
                )
                pool.cmd_add(args)

                # Check pool index entry
                idx = pool.load_pool_index()
                self.assertEqual(len(idx["items"]), 1)
                entry = idx["items"][0]
                self.assertTrue(entry.get("is_pilot"), "index entry is_pilot should be True")
                self.assertEqual(entry.get("artifact_type"), "pilot")

                # Check item file
                item_id = entry["id"]
                item_path = pool_root / "pending" / f"{item_id}.json"
                self.assertTrue(item_path.exists(), f"Item file {item_path} not found")
                item_data = _read_json(item_path)
                self.assertTrue(item_data.get("is_pilot"), "item is_pilot should be True")
                self.assertEqual(item_data.get("artifact_type"), "pilot")

    def test_title_add_pilot_false_writes_is_pilot_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                args = _make_args(
                    title="Regular task",
                    layer="L2_bug_fix",
                    risk="MEDIUM",
                    priority=2,
                    pilot=False,
                )
                pool.cmd_add(args)

                idx = pool.load_pool_index()
                self.assertEqual(len(idx["items"]), 1)
                entry = idx["items"][0]
                self.assertFalse(entry.get("is_pilot"), "index entry is_pilot should be False")
                self.assertEqual(entry.get("artifact_type"), "task")

                item_id = entry["id"]
                item_path = pool_root / "pending" / f"{item_id}.json"
                item_data = _read_json(item_path)
                self.assertFalse(item_data.get("is_pilot"), "item is_pilot should be False")
                self.assertEqual(item_data.get("artifact_type"), "task")


# ---------------------------------------------------------------------------
# Tests — cmd_add() file-based with pilot metadata
# ---------------------------------------------------------------------------

class TestCmdAddFileBased(unittest.TestCase):
    """File-based add: loaded pilot metadata preserved; CLI --pilot overrides."""

    def test_file_with_pilot_metadata_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            # Create source file with is_pilot:true
            source_file = Path(tmpdir) / "task.json"
            source_file.write_text(
                json.dumps({
                    "title": "Loaded pilot from file",
                    "layer": "L1_feature_dev",
                    "is_pilot": True,
                }),
                encoding="utf-8",
            )

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                args = _make_args(file=str(source_file), pilot=False)
                pool.cmd_add(args)

                idx = pool.load_pool_index()
                entry = idx["items"][0]
                self.assertTrue(entry.get("is_pilot"), "loaded is_pilot should be preserved")
                self.assertEqual(entry.get("artifact_type"), "pilot")

                item_id = entry["id"]
                item_path = pool_root / "pending" / f"{item_id}.json"
                item_data = _read_json(item_path)
                self.assertTrue(item_data.get("is_pilot"))
                self.assertEqual(item_data.get("artifact_type"), "pilot")

    def test_file_cli_pilot_overrides_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            # Create source file with is_pilot:false
            source_file = Path(tmpdir) / "task.json"
            source_file.write_text(
                json.dumps({
                    "title": "Non-pilot in file",
                    "layer": "L1_feature_dev",
                    "is_pilot": False,
                }),
                encoding="utf-8",
            )

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                # CLI --pilot overrides loaded value
                args = _make_args(file=str(source_file), pilot=True)
                pool.cmd_add(args)

                idx = pool.load_pool_index()
                entry = idx["items"][0]
                self.assertTrue(entry.get("is_pilot"), "CLI --pilot should override loaded False")
                self.assertEqual(entry.get("artifact_type"), "pilot")

    def test_file_without_pilot_metadata_defaults_to_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            source_file = Path(tmpdir) / "task.json"
            source_file.write_text(
                json.dumps({
                    "title": "No pilot metadata in file",
                    "layer": "L2_bug_fix",
                }),
                encoding="utf-8",
            )

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                args = _make_args(file=str(source_file), pilot=False)
                pool.cmd_add(args)

                idx = pool.load_pool_index()
                entry = idx["items"][0]
                self.assertFalse(entry.get("is_pilot"), "default is_pilot should be False")
                self.assertEqual(entry.get("artifact_type"), "task")


# ---------------------------------------------------------------------------
# Tests — pool index append vs update
# ---------------------------------------------------------------------------

class TestPoolIndexUpdate(unittest.TestCase):
    """Re-adding the same ID updates the existing index entry (including pilot metadata)."""

    def test_re_add_updates_existing_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()

                # First add: non-pilot
                args1 = _make_args(
                    title="First task",
                    layer="L2_bug_fix",
                    priority=1,
                    pilot=False,
                )
                pool.cmd_add(args1)

                idx1 = pool.load_pool_index()
                item_id = idx1["items"][0]["id"]

                # Second add: same title (or same item) as pilot
                # Write a temp file with that ID
                tmp_task = Path(tmpdir) / "update.json"
                tmp_task.write_text(
                    json.dumps({
                        "id": item_id,
                        "title": "Updated task",
                        "layer": "L1_feature_dev",
                        "is_pilot": True,
                    }),
                    encoding="utf-8",
                )
                args2 = _make_args(file=str(tmp_task), pilot=False)
                pool.cmd_add(args2)

                idx2 = pool.load_pool_index()
                self.assertEqual(len(idx2["items"]), 1, "re-add should update, not duplicate")

                entry = idx2["items"][0]
                self.assertEqual(entry["id"], item_id)
                self.assertTrue(entry.get("is_pilot"), "updated entry is_pilot should be True")
                self.assertEqual(entry["artifact_type"], "pilot")


# ---------------------------------------------------------------------------
# Tests — load_pool_index()
# ---------------------------------------------------------------------------

class TestLoadPoolIndex(unittest.TestCase):
    """load_pool_index() handles missing/empty files gracefully."""

    def test_missing_pool_index_returns_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                result = pool.load_pool_index()
                self.assertEqual(result["items"], [])
                self.assertEqual(result["version"], "v1.0")

    def test_empty_pool_index_returns_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            # Create parent directory before writing empty file
            pool_root.mkdir(parents=True)
            pool_index.write_text("", encoding="utf-8")

            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                result = pool.load_pool_index()
                self.assertEqual(result["items"], [])
                self.assertEqual(result["version"], "v1.0")


# ---------------------------------------------------------------------------
# Tests — ensure pool dirs
# ---------------------------------------------------------------------------

class TestEnsurePoolDirs(unittest.TestCase):
    """ensure_pool_dirs() creates all required subdirectories."""

    def test_creates_all_subdirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"

            with patch.object(pool, "POOL_ROOT", pool_root):
                pool.ensure_pool_dirs()
                for subdir in pool.SUBDIRS:
                    self.assertTrue(
                        (pool_root / subdir).exists(),
                        f"{subdir} was not created",
                    )


# ---------------------------------------------------------------------------
# Tests — cmd_list()
# ---------------------------------------------------------------------------

class TestCmdList(unittest.TestCase):
    """cmd_list() lists pool items, optionally filtered by status."""

    def _make_list_args(self, status=None):
        return Namespace(status=status)

    def test_list_empty_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                args = self._make_list_args()
                pool.cmd_list(args)

    def test_list_with_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_add(_make_args(
                    title="Task A", layer="L2_bug_fix", priority=2
                ))
                pool.cmd_add(_make_args(
                    title="Task B", layer="L1_feature_dev", priority=1
                ))
                idx = pool.load_pool_index()
                self.assertEqual(len(idx["items"]), 2)

    def test_list_filter_by_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_add(_make_args(title="Task A", layer="L2_bug_fix"))
                args = self._make_list_args(status="completed")
                pool.cmd_list(args)


# ---------------------------------------------------------------------------
# Tests — cmd_pick()
# ---------------------------------------------------------------------------

class TestCmdPick(unittest.TestCase):
    """cmd_pick() picks next available pending task."""

    def test_pick_next_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_add(_make_args(
                    title="Task A", layer="L2_bug_fix", priority=1
                ))
                pool.cmd_pick(None)
                idx = pool.load_pool_index()
                self.assertEqual(idx["items"][0]["status"], pool.STATUS_PICKED)

    def test_pick_empty_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_pick(None)

    def test_pick_respects_priority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_add(_make_args(
                    title="Low priority", layer="L2_bug_fix", priority=10
                ))
                pool.cmd_add(_make_args(
                    title="High priority", layer="L1_feature_dev", priority=1
                ))
                pool.cmd_pick(None)
                idx = pool.load_pool_index()
                picked = [i for i in idx["items"] if i["status"] == pool.STATUS_PICKED]
                self.assertEqual(len(picked), 1, "Expected exactly one picked item")
                self.assertEqual(picked[0]["title"], "High priority",
                                 "Higher-priority item should be picked first")


# ---------------------------------------------------------------------------
# Tests — cmd_complete()
# ---------------------------------------------------------------------------

class TestCmdComplete(unittest.TestCase):
    """cmd_complete() marks a task as completed."""

    def _make_complete_args(self, task_id):
        return Namespace(task_id=task_id)

    def test_complete_picked_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_add(_make_args(
                    title="Task A", layer="L2_bug_fix", priority=1
                ))
                pool.cmd_pick(None)
                idx = pool.load_pool_index()
                item_id = idx["items"][0]["id"]
                args = self._make_complete_args(item_id)
                pool.cmd_complete(args)
                idx2 = pool.load_pool_index()
                self.assertEqual(idx2["items"][0]["status"], pool.STATUS_COMPLETED)

    def test_complete_invalid_status_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_add(_make_args(
                    title="Task A", layer="L2_bug_fix", priority=1
                ))
                idx = pool.load_pool_index()
                item_id = idx["items"][0]["id"]
                args = self._make_complete_args(item_id)
                with self.assertRaises(SystemExit):
                    pool.cmd_complete(args)

    def test_complete_missing_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                args = self._make_complete_args("pool-nonexistent-999")
                with self.assertRaises(SystemExit):
                    pool.cmd_complete(args)


# ---------------------------------------------------------------------------
# Tests — cmd_status()
# ---------------------------------------------------------------------------

class TestCmdStatus(unittest.TestCase):
    """cmd_status() shows task details."""

    def _make_status_args(self, task_id):
        return Namespace(task_id=task_id)

    def test_status_existing_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                pool.cmd_add(_make_args(
                    title="Task A", layer="L2_bug_fix", priority=1
                ))
                idx = pool.load_pool_index()
                item_id = idx["items"][0]["id"]
                args = self._make_status_args(item_id)
                pool.cmd_status(args)

    def test_status_missing_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pool_root = Path(tmpdir) / "pool"
            pool_index = pool_root / "pool.yaml"
            with patch.object(pool, "POOL_ROOT", pool_root), \
                 patch.object(pool, "POOL_INDEX", pool_index):
                pool.ensure_pool_dirs()
                pool.cmd_init()
                args = self._make_status_args("pool-nonexistent-999")
                with self.assertRaises(SystemExit):
                    pool.cmd_status(args)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
