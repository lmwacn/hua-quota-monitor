from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.local_project_sync import LocalProjectSync, merge_local_projects


PROFILE = "electron-account-renderer-state-v1"
ATOMS = "electron-persisted-atom-state"
PINS = "unified-sidebar-pinned-order-v1"
ORDER = "unified-sidebar-project-order-v1"
SORT = "flat-project-sidebar-preferences-v1"


def project(name):
    return {"id": name, "name": name, "rootPaths": [f"/tmp/{name}"], "updatedAt": 1}


def state_with_accounts():
    return {
        PROFILE: {
            "owner": "b",
            "inactive": {
                "a": {
                    "globals": {
                        "local-projects": {"one": project("one"), "two": project("two")},
                        "project-order": ["two", "one"],
                        "pinned-project-ids": ["two", "remote"],
                        "thread-project-assignments": {"private": "one"},
                    },
                    "atoms": {
                        PINS: ["codex:thread:local:private", "codex:project:two"],
                        ORDER: ["codex:project:two", "codex:project:one"],
                        SORT: {"projectSortMode": "manual", "manualSortVersion": 1},
                    },
                },
            },
        },
        ATOMS: {
            PINS: ["chatgpt:conversation:another"],
            SORT: {"chatSortMode": "updated_at", "projectSortMode": "updated_at"},
        },
    }


class LocalProjectSyncTests(unittest.TestCase):
    def test_seeds_local_layout_without_copying_other_account_data(self):
        state = state_with_accounts()
        baseline = merge_local_projects(state, {})
        self.assertEqual(state["project-order"], ["two", "one"])
        self.assertEqual(state["pinned-project-ids"], ["two"])
        self.assertEqual(state[ATOMS][PINS], ["chatgpt:conversation:another", "codex:project:two"])
        self.assertEqual(state[ATOMS][SORT]["chatSortMode"], "updated_at")
        self.assertEqual(state[ATOMS][SORT]["manualSortVersion"], 1)
        self.assertNotIn("thread-project-assignments", state)
        before = copy.deepcopy(state)
        self.assertEqual(merge_local_projects(state, baseline), baseline)
        self.assertEqual(state, before)

    def test_either_account_can_edit_and_unpin(self):
        state = state_with_accounts()
        baseline = merge_local_projects(state, {})
        state["project-order"] = ["one", "two"]
        state[ATOMS][ORDER] = ["codex:project:one", "codex:project:two"]
        other = state[PROFILE]["inactive"]["a"]
        other["globals"]["pinned-project-ids"] = ["remote"]
        other["atoms"][PINS] = ["codex:thread:local:private"]
        baseline = merge_local_projects(state, baseline)
        self.assertEqual(other["globals"]["project-order"], ["one", "two"])
        self.assertEqual(state["pinned-project-ids"], [])
        self.assertEqual(state[ATOMS][PINS], ["chatgpt:conversation:another"])
        other["globals"]["project-order"] = ["two", "one"]
        other["atoms"][ORDER] = ["codex:project:two", "codex:project:one"]
        state["local-projects"]["three"] = project("three")
        merge_local_projects(state, baseline)
        self.assertEqual(state["project-order"], ["two", "one", "three"])
        self.assertIn("three", other["globals"]["local-projects"])

    def test_same_layout_conflict_prefers_the_current_editor(self):
        state = state_with_accounts()
        baseline = merge_local_projects(state, {})
        other = state[PROFILE]["inactive"]["a"]
        other["globals"]["pinned-project-ids"] = ["one", "remote"]
        state["pinned-project-ids"] = []
        merge_local_projects(state, baseline)
        self.assertEqual(other["globals"]["pinned-project-ids"], ["remote"])

    def test_file_sync_waits_for_exit_and_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            original = json.dumps(state_with_accounts()).encode()
            path.write_bytes(original)
            sync = LocalProjectSync(state_path=path, store_dir=Path(folder))
            with patch("src.local_project_sync.desktop_running", return_value=True):
                self.assertFalse(sync.sync())
            self.assertEqual(path.read_bytes(), original)
            with patch("src.local_project_sync.desktop_running", return_value=False):
                self.assertTrue(sync.sync())
                self.assertFalse(sync.sync())
            self.assertEqual((sync.directory / "last-backup.json").read_bytes(), original)
            self.assertEqual(json.loads(path.read_bytes())["pinned-project-ids"], ["two"])
