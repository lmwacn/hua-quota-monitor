from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from src.web_auth import (
    CodexNotFoundError,
    InvalidWebAuthResultError,
    WebAuthCommandError,
    WebAuthTimeoutError,
    _resolve_codex_executable,
    login_with_chatgpt,
)


class WebAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _fake_codex(self, body: str) -> Path:
        path = self.base / f"fake-codex-{len(list(self.base.glob('fake-codex-*')))}"
        path.write_text(
            f"#!{sys.executable}\n" + textwrap.dedent(body),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_success_uses_isolated_home_and_cleans_it(self) -> None:
        marker = self.base / "isolated-home.txt"
        path_marker = self.base / "login-path.txt"
        normal_home = self.base / "normal-codex-home"
        normal_home.mkdir()
        canonical = normal_home / "auth.json"
        canonical.write_text('{"normal": true}', encoding="utf-8")
        credential = {"auth_mode": "chatgpt", "tokens": {"secret": "private"}}
        fake = self._fake_codex(
            """
            import json
            import os
            import pathlib
            import sys

            assert sys.argv[1:] == [
                "login", "-c", 'cli_auth_credentials_store="file"'
            ]
            home = pathlib.Path(os.environ["CODEX_HOME"])
            pathlib.Path(os.environ["WEB_AUTH_TEST_MARKER"]).write_text(str(home))
            pathlib.Path(os.environ["WEB_AUTH_PATH_MARKER"]).write_text(os.environ["PATH"])
            (home / "auth.json").write_text(json.dumps({
                "auth_mode": "chatgpt",
                "tokens": {"secret": "private"},
            }))
            """
        )

        with mock.patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(normal_home),
                "WEB_AUTH_TEST_MARKER": str(marker),
                "WEB_AUTH_PATH_MARKER": str(path_marker),
                "PATH": "/usr/bin:/bin",
            },
        ):
            result = login_with_chatgpt(codex_executable=fake, timeout=5)

        isolated_home = Path(marker.read_text(encoding="utf-8"))
        self.assertNotEqual(isolated_home, normal_home)
        self.assertFalse(isolated_home.exists())
        self.assertEqual(json.loads(result), credential)
        self.assertEqual(canonical.read_text(encoding="utf-8"), '{"normal": true}')
        login_path = path_marker.read_text(encoding="utf-8").split(os.pathsep)
        self.assertEqual(login_path[0], str(fake.parent))
        self.assertIn("/usr/local/bin", login_path)

    def test_finds_npm_prefix_when_gui_path_has_no_codex(self) -> None:
        npm_prefix = self.base / "npm-prefix"
        fake = npm_prefix / "bin" / "codex"
        fake.parent.mkdir(parents=True)
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

        with mock.patch.dict(
            os.environ,
            {"PATH": "/usr/bin:/bin", "NPM_CONFIG_PREFIX": str(npm_prefix)},
            clear=False,
        ), mock.patch("src.web_auth.shutil.which", return_value=None):
            self.assertEqual(_resolve_codex_executable(None), str(fake))

    def test_missing_codex_has_clear_error(self) -> None:
        missing = self.base / "does-not-exist"
        with self.assertRaisesRegex(CodexNotFoundError, "未找到 codex"):
            login_with_chatgpt(codex_executable=missing)

    def test_nonzero_exit_has_code_without_command_output(self) -> None:
        fake = self._fake_codex(
            """
            import sys
            print("must-not-leak-secret", file=sys.stderr)
            raise SystemExit(23)
            """
        )
        with self.assertRaises(WebAuthCommandError) as captured:
            login_with_chatgpt(codex_executable=fake, timeout=5)
        self.assertEqual(captured.exception.returncode, 23)
        self.assertNotIn("must-not-leak-secret", str(captured.exception))

    def test_timeout_terminates_login_and_cleans_home(self) -> None:
        marker = self.base / "timeout-home.txt"
        fake = self._fake_codex(
            """
            import os
            import pathlib
            import time
            pathlib.Path(os.environ["WEB_AUTH_TEST_MARKER"]).write_text(
                os.environ["CODEX_HOME"]
            )
            time.sleep(30)
            """
        )
        with mock.patch.dict(os.environ, {"WEB_AUTH_TEST_MARKER": str(marker)}):
            with self.assertRaisesRegex(WebAuthTimeoutError, "授权超时"):
                login_with_chatgpt(codex_executable=fake, timeout=0.5)
        self.assertFalse(Path(marker.read_text(encoding="utf-8")).exists())

    def test_success_without_auth_file_is_rejected_and_cleaned(self) -> None:
        marker = self.base / "empty-home.txt"
        fake = self._fake_codex(
            """
            import os
            import pathlib
            pathlib.Path(os.environ["WEB_AUTH_TEST_MARKER"]).write_text(
                os.environ["CODEX_HOME"]
            )
            """
        )
        with mock.patch.dict(os.environ, {"WEB_AUTH_TEST_MARKER": str(marker)}):
            with self.assertRaisesRegex(InvalidWebAuthResultError, "auth.json"):
                login_with_chatgpt(codex_executable=fake, timeout=5)
        self.assertFalse(Path(marker.read_text(encoding="utf-8")).exists())


if __name__ == "__main__":
    unittest.main()
