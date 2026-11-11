from __future__ import annotations

import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.local_project_sync import merge_local_projects
from src.local_sidebar_sync import SECTIONS, account_ids
from test_local_project_sync import ATOMS, PROFILE, state_with_accounts


def section(section_id="ui-a", host="backend-one", items=None):
    return {"id": section_id, "name": "开发", "hostSectionIds": {"local": host},
            "itemKeys": items if items is not None else ["codex:thread:local:task", "codex:project:one"]}


def data(sections):
    return {"sections": sections, "collapsedSectionIds": [],
            "sectionOrder": ["pinned", *(f"custom:{s['id']}" for s in sections), "threads"]}


class SidebarSyncTests(unittest.TestCase):
    def setUp(self):
        self.state = state_with_accounts()
        self.other = self.state[PROFILE]["inactive"]["a"]["atoms"]
        self.other[SECTIONS] = {"account-a": data([section()]), "unrelated": {"keep": True}}
        self.identities = {"a": "account-a", "b": "account-b"}

    def sync(self, previous=None):
        return merge_local_projects(self.state, previous or {}, self.identities)

    def current(self):
        return self.state[ATOMS][SECTIONS]["account-b"]

    def test_seeds_and_is_idempotent_without_copying_private_content(self):
        source = self.other[SECTIONS]["account-a"]
        source["sections"][0]["itemKeys"].extend(["chatgpt:conversation:secret", "codex:thread:remote:secret"])
        source["sections"][0]["pendingDeletionHostIdsByItemKey"] = {
            "codex:thread:local:task": ["local"], "chatgpt:conversation:secret": ["remote"],
        }
        baseline = self.sync()
        self.assertEqual(self.current()["sections"][0]["itemKeys"], ["codex:thread:local:task", "codex:project:one"])
        self.assertEqual(self.current()["threadHostIds"], {"task": "local"})
        self.assertEqual(self.current()["sections"][0]["pendingDeletionHostIdsByItemKey"], {
            "codex:thread:local:task": ["local"],
        })
        self.assertEqual(self.other[SECTIONS]["unrelated"], {"keep": True})
        before = copy.deepcopy(self.state)
        self.assertEqual(self.sync(baseline), baseline)
        self.assertEqual(self.state, before)

    def test_matches_backend_id_preserving_destination_ui_id_and_private_items(self):
        self.state[ATOMS][SECTIONS] = {"account-b": data([section("ui-b", items=["chatgpt:conversation:own"])])}
        baseline = self.sync()
        dest = self.current()
        dest["sections"][0]["name"] = "新名称"
        dest["sections"][0]["itemKeys"] = ["chatgpt:conversation:own", "codex:project:two"]
        dest["collapsedSectionIds"] = ["ui-b"]
        self.sync(baseline)
        source = self.other[SECTIONS]["account-a"]
        self.assertEqual(len(dest["sections"]), 1)
        self.assertEqual(dest["sections"][0]["id"], "ui-b")
        self.assertEqual(source["sections"][0]["id"], "ui-a")
        self.assertEqual(source["sections"][0]["name"], "新名称")
        self.assertEqual(source["sections"][0]["itemKeys"], ["codex:project:two"])
        self.assertEqual(source["collapsedSectionIds"], ["ui-a"])
        self.assertIn("chatgpt:conversation:own", dest["sections"][0]["itemKeys"])

    def test_deletion_propagates_and_keeps_private_group_content(self):
        source = self.other[SECTIONS]["account-a"]
        source["sections"][0]["itemKeys"].append("chatgpt:conversation:secret")
        baseline = self.sync()
        self.current().update(sections=[], sectionOrder=["pinned", "threads"])
        baseline = self.sync(baseline)
        self.assertEqual(source["sections"][0]["itemKeys"], ["chatgpt:conversation:secret"])
        self.assertEqual(source["sections"][0]["hostSectionIds"], {})
        before = copy.deepcopy(self.state)
        self.sync(baseline)
        self.assertEqual(before, self.state)

    def test_reorders_moves_and_deletes_groups(self):
        baseline = self.sync()
        dest = self.current()
        dest["sections"].append(section("new", "backend-two", ["codex:project:two"]))
        dest["sections"][0]["itemKeys"] = []
        dest["sectionOrder"] = ["pinned", "custom:new", "custom:backend-one", "threads"]
        baseline = self.sync(baseline)
        source = self.other[SECTIONS]["account-a"]
        self.assertEqual(source["sectionOrder"], ["pinned", "custom:backend-two", "custom:ui-a", "threads"])
        dest["sections"] = []
        dest["sectionOrder"] = ["pinned", "threads"]
        self.sync(baseline)
        self.assertEqual(source["sections"], [])

    def test_current_editor_wins_conflict(self):
        baseline = self.sync()
        self.current()["sections"][0]["name"] = "当前"
        self.other[SECTIONS]["account-a"]["sections"][0]["name"] = "其他"
        self.sync(baseline)
        self.assertEqual(self.other[SECTIONS]["account-a"]["sections"][0]["name"], "当前")

    def test_groups_sync_without_local_projects_and_with_old_baseline(self):
        self.other[SECTIONS]["account-a"]["sections"][0]["itemKeys"] = ["codex:thread:local:task"]
        self.state[PROFILE]["inactive"]["a"]["globals"]["local-projects"] = {}
        self.sync()
        self.assertEqual(self.current()["sections"][0]["name"], "开发")
        baseline = {key: {k: v for k, v in entry.items() if k != "sections"} for key, entry in self.sync().items()}
        self.sync(baseline)
        self.assertEqual(len(self.current()["sections"]), 1)

    def test_remote_only_and_unidentified_accounts_are_untouched(self):
        private = section("private", items=["chatgpt:conversation:own"])
        private["hostSectionIds"] = {"remote": "remote-section"}
        self.state[ATOMS][SECTIONS] = {"account-b": data([private])}
        self.sync()
        self.assertEqual(self.current()["sections"][0], private)
        state = state_with_accounts()
        before = copy.deepcopy(state[ATOMS])
        merge_local_projects(state, {})
        self.assertNotIn(SECTIONS, state[ATOMS])
        self.assertNotIn(SECTIONS, before)

    def test_identity_hash_matches_client_without_writing_credentials(self):
        claims = {"https://api.openai.com/auth": {"chatgpt_user_id": "user", "chatgpt_account_id": "account"}}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(json.dumps({"tokens": {"id_token": f"header.{encoded}.signature"}}))
            original = path.read_bytes()
            expected = hashlib.sha256(b'["user","account"]').hexdigest()
            self.assertEqual(account_ids([path, path.with_name("missing")]), {expected: "account"})
            self.assertEqual(path.read_bytes(), original)

    def test_legacy_group_is_migrated_and_missing_credentials_keep_baseline(self):
        source = self.other[SECTIONS]["account-a"]
        source["sections"][0].pop("hostSectionIds")
        self.state[ATOMS][SECTIONS] = {"account-b": {
            **data([]), "appServerMigratedHostIds": ["local", "remote"],
        }}
        baseline = self.sync()
        self.assertEqual(self.current()["appServerLegacySectionIds"], ["ui-a"])
        self.assertEqual(self.current()["appServerMigratedHostIds"], ["remote"])
        preserved = merge_local_projects(self.state, baseline, {})
        self.assertEqual(preserved["b"]["sections"], baseline["b"]["sections"])
