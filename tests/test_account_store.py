from __future__ import annotations

import base64
import json
import stat
import tempfile
import unittest
from pathlib import Path

from src.account_store import (
    AccountExistsError,
    AccountNotFoundError,
    AccountStore,
    AccountStoreError,
    InvalidAccountNameError,
    InvalidAuthFileError,
    InvalidDisplayNameError,
)


def _auth(account_id: str, email: str, access_token: str) -> bytes:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    claims = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode("utf-8")
    ).decode().rstrip("=")
    id_token = f"{header}.{claims}.signature"
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": access_token,
                "refresh_token": f"refresh-{access_token}",
                "account_id": account_id,
                "id_token": id_token,
            },
        }
    ).encode("utf-8")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class AccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "store"
        self.canonical = base / "codex" / "auth.json"
        self.store = AccountStore(root=self.root, canonical_auth_path=self.canonical)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _source(self, filename: str, payload: bytes) -> Path:
        path = Path(self.temporary.name) / filename
        path.write_bytes(payload)
        return path

    def test_import_list_and_current_keep_tokens_out_of_state(self) -> None:
        source = self._source("personal.json", _auth("acct-a", "a@example.com", "secret-a"))
        profile = self.store.import_account("personal", source, make_current=True)

        self.assertEqual(profile.name, "personal")
        self.assertEqual(profile.display_name, "personal")
        self.assertEqual(profile.account_id, "acct-a")
        self.assertEqual(profile.email, "a@example.com")
        self.assertTrue(profile.is_current)
        self.assertEqual(self.store.get_current().name, "personal")
        self.assertEqual([item.name for item in self.store.list_accounts()], ["personal"])
        self.assertNotIn("secret-a", self.store.state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            json.loads(self.store.state_path.read_text()),
            {
                "version": 2,
                "current": "personal",
                "profiles": {"personal": {"display_name": "personal"}},
            },
        )

        self.assertEqual(_mode(self.root), 0o700)
        self.assertEqual(_mode(self.store.accounts_dir), 0o700)
        self.assertEqual(_mode(profile.auth_path.parent), 0o700)
        self.assertEqual(_mode(profile.auth_path), 0o600)
        self.assertEqual(_mode(self.store.state_path), 0o600)
        self.assertEqual(_mode(self.store.lock_path), 0o600)

    def test_existing_canonical_auth_permissions_are_tightened(self) -> None:
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_bytes(_auth("acct-a", "a@example.com", "secret-a"))
        self.canonical.chmod(0o644)

        AccountStore(root=self.root, canonical_auth_path=self.canonical)

        self.assertEqual(_mode(self.canonical), 0o600)

    def test_rejects_unsafe_names_invalid_auth_and_implicit_overwrite(self) -> None:
        source = self._source("valid.json", _auth("acct-a", "a@example.com", "secret-a"))
        for name in ("", ".", "../escape", "two words", "个人", "-leading", "a" * 65):
            with self.subTest(name=name):
                with self.assertRaises(InvalidAccountNameError):
                    self.store.import_account(name, source)

        invalid = self._source("invalid.json", b'{"tokens": {}}')
        with self.assertRaises(InvalidAuthFileError):
            self.store.import_account("invalid", invalid)

        self.store.import_account("personal", source)
        with self.assertRaises(AccountExistsError):
            self.store.import_account("personal", source)
        with self.assertRaises(AccountNotFoundError):
            self.store.set_current("missing")

    def test_import_and_rename_support_user_facing_display_names(self) -> None:
        source = self._source("personal.json", _auth("acct-a", "a@example.com", "secret-a"))

        imported = self.store.import_account(
            "personal", source, display_name="  个人 Pro 账号  "
        )
        self.assertEqual(imported.name, "personal")
        self.assertEqual(imported.display_name, "个人 Pro 账号")

        renamed = self.store.set_display_name("personal", "工作号")
        self.assertEqual(renamed.display_name, "工作号")
        self.assertEqual(self.store.get_account("personal").display_name, "工作号")
        self.assertEqual(
            json.loads(self.store.state_path.read_text())["profiles"]["personal"],
            {"display_name": "工作号"},
        )

    def test_display_name_validation_and_missing_profile(self) -> None:
        source = self._source("personal.json", _auth("acct-a", "a@example.com", "secret-a"))
        self.store.import_account("personal", source)

        for display_name in ("", "   ", "bad\nname", "bad\x00name", "a" * 65):
            with self.subTest(display_name=repr(display_name)):
                with self.assertRaises(InvalidDisplayNameError):
                    self.store.set_display_name("personal", display_name)
        with self.assertRaises(InvalidDisplayNameError):
            self.store.import_account("second", source, display_name="\t")
        with self.assertRaises(AccountNotFoundError):
            self.store.set_display_name("missing", "新名称")

    def test_version_one_state_is_migrated_with_slug_fallbacks(self) -> None:
        personal = self._source("personal.json", _auth("acct-a", "a@example.com", "secret-a"))
        work = self._source("work.json", _auth("acct-b", "b@example.com", "secret-b"))
        self.store.import_account("personal", personal)
        self.store.import_account("work", work)
        self.store.state_path.write_text(
            json.dumps({"version": 1, "current": "work"}), encoding="utf-8"
        )

        profiles = self.store.list_accounts()

        self.assertEqual(
            [(profile.name, profile.display_name) for profile in profiles],
            [("personal", "personal"), ("work", "work")],
        )
        self.assertTrue(self.store.get_current().is_current)
        self.assertEqual(
            json.loads(self.store.state_path.read_text()),
            {
                "version": 2,
                "current": "work",
                "profiles": {
                    "personal": {"display_name": "personal"},
                    "work": {"display_name": "work"},
                },
            },
        )

    def test_detect_and_activate_snapshots_previous_profile(self) -> None:
        old_a = self._source("old-a.json", _auth("acct-a", "a@example.com", "old-a"))
        account_b = self._source("b.json", _auth("acct-b", "b@example.com", "token-b"))
        self.store.import_account("personal", old_a)
        self.store.import_account("work", account_b)
        self.canonical.parent.mkdir(parents=True)
        refreshed_a = _auth("acct-a", "a@example.com", "refreshed-a")
        self.canonical.write_bytes(refreshed_a)

        self.assertEqual(self.store.detect_active_name(), "personal")
        result = self.store.activate("work")

        self.assertEqual(result.previous.name, "personal")
        self.assertEqual(result.activated.name, "work")
        self.assertTrue(result.activated.is_active)
        self.assertTrue(result.activated.is_current)
        self.assertIsNone(result.backup_path)
        self.assertEqual(self.store.get_account("personal").auth_path.read_bytes(), refreshed_a)
        self.assertEqual(self.canonical.read_bytes(), account_b.read_bytes())
        self.assertEqual(_mode(self.canonical.parent), 0o700)
        self.assertEqual(_mode(self.canonical), 0o600)
        self.assertEqual(self.store.get_current().name, "work")

    def test_activate_preserves_unknown_canonical_in_protected_backup(self) -> None:
        target = self._source("target.json", _auth("acct-b", "b@example.com", "token-b"))
        self.store.import_account("work", target)
        self.canonical.parent.mkdir(parents=True)
        unknown = _auth("acct-unknown", "unknown@example.com", "unknown-secret")
        self.canonical.write_bytes(unknown)

        result = self.store.activate("work")

        self.assertIsNone(result.previous)
        self.assertIsNotNone(result.backup_path)
        self.assertEqual(result.backup_path.read_bytes(), unknown)
        self.assertEqual(_mode(result.backup_path), 0o600)
        self.assertEqual(_mode(result.backup_path.parent), 0o700)
        self.assertEqual(self.canonical.read_bytes(), target.read_bytes())

    def test_reactivating_active_account_keeps_refreshed_credential(self) -> None:
        original = self._source("personal.json", _auth("acct-a", "a@example.com", "old"))
        self.store.import_account("personal", original)
        self.canonical.parent.mkdir(parents=True)
        refreshed = _auth("acct-a", "a@example.com", "new")
        self.canonical.write_bytes(refreshed)

        self.store.activate("personal")

        self.assertEqual(self.store.get_account("personal").auth_path.read_bytes(), refreshed)
        self.assertEqual(self.canonical.read_bytes(), refreshed)

    def test_account_symlink_cannot_escape_store(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.store.accounts_dir / "linked").symlink_to(outside, target_is_directory=True)
        source = self._source("valid.json", _auth("acct-a", "a@example.com", "secret"))

        with self.assertRaisesRegex(AccountStoreError, "符号链接"):
            self.store.import_account("linked", source)
        self.assertFalse((outside / "auth.json").exists())


if __name__ == "__main__":
    unittest.main()
