from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = PROJECT_ROOT / "main.py"


def _auth(account_id: str, email: str, access_token: str) -> bytes:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    claims = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode("utf-8")
    ).decode().rstrip("=")
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": access_token,
                "refresh_token": f"refresh-{access_token}",
                "account_id": account_id,
                "id_token": f"{header}.{claims}.signature",
            },
        }
    ).encode("utf-8")


class AccountCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.store = base / "account-store"
        self.canonical = base / "codex" / "auth.json"
        self.personal = base / "personal.json"
        self.work = base / "work.json"
        self.personal.write_bytes(_auth("acct-personal", "personal@example.com", "personal-old"))
        self.work.write_bytes(_auth("acct-work", "work@example.com", "work-token"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(MAIN_PATH),
            "account",
            "--store-dir",
            str(self.store),
            "--codex-auth-file",
            str(self.canonical),
            *arguments,
        ]
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_import_list_current_and_use(self) -> None:
        no_current = self._run("current")
        self.assertEqual(no_current.returncode, 1)
        self.assertIn("未设置顶栏主账号", no_current.stdout)

        imported_personal = self._run(
            "import", "personal", "--auth-file", str(self.personal)
        )
        self.assertEqual(imported_personal.returncode, 0, imported_personal.stderr)
        self.assertIn("已导入账号：personal", imported_personal.stdout)
        # The first imported account becomes the monitoring account by default.
        current = self._run("current")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(current.stdout.strip(), "personal\tpersonal@example.com")

        imported_work = self._run("import", "work", "--auth-file", str(self.work))
        self.assertEqual(imported_work.returncode, 0, imported_work.stderr)

        listed = self._run("list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        profiles = json.loads(listed.stdout)
        self.assertEqual([profile["name"] for profile in profiles], ["personal", "work"])
        self.assertEqual([profile["display_name"] for profile in profiles], ["personal", "work"])
        self.assertEqual(profiles[0]["email"], "personal@example.com")
        self.assertTrue(profiles[0]["monitoring"])
        self.assertFalse(profiles[1]["monitoring"])
        # list JSON is metadata-only and must not expose credentials.
        self.assertNotIn("personal-old", listed.stdout)
        self.assertNotIn("work-token", listed.stdout)

        selected = self._run("use", "work")
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertIn("顶栏主账号已设为：work", selected.stdout)
        current = self._run("current")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(current.stdout.strip(), "work\twork@example.com")

        state = json.loads((self.store / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state,
            {
                "version": 2,
                "current": "work",
                "profiles": {
                    "personal": {"display_name": "personal"},
                    "work": {"display_name": "work"},
                },
            },
        )

    def test_import_with_display_name_and_rename(self) -> None:
        imported = self._run(
            "import",
            "personal",
            "--display-name",
            "个人 Pro",
            "--auth-file",
            str(self.personal),
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertIn("已导入账号：个人 Pro", imported.stdout)

        renamed = self._run("rename", "personal", "日常账号")
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        self.assertIn("账号已重命名为：日常账号", renamed.stdout)

        listed = json.loads(self._run("list", "--json").stdout)
        self.assertEqual(listed[0]["name"], "personal")
        self.assertEqual(listed[0]["display_name"], "日常账号")

    def test_cli_errors_return_one_without_touching_canonical(self) -> None:
        first = self._run("import", "personal", "--auth-file", str(self.personal))
        self.assertEqual(first.returncode, 0, first.stderr)

        duplicate = self._run("import", "personal", "--auth-file", str(self.work))
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("账号操作失败：账号已存在", duplicate.stderr)

        missing = self._run("use", "missing")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("账号操作失败：账号不存在", missing.stderr)
        self.assertFalse(self.canonical.exists())


if __name__ == "__main__":
    unittest.main()
