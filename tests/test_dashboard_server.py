from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from src.dashboard_server import create_dashboard_server, find_available_port
from src.usage_monitor import UsageMonitor


def _usage(used: float = 7) -> dict:
    return {
        "account_id": "account-1",
        "rate_limit": {
            "primary_window": {
                "used_percent": used,
                "limit_window_seconds": 604_800,
                "reset_at": 2_000_000_000,
            }
        },
    }


class DashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.auth_file = root / "auth.json"
        self.auth_file.write_text(
            json.dumps(
                {"tokens": {"access_token": "test", "account_id": "account-1"}}
            ),
            encoding="utf-8",
        )
        self.store_dir = root / "store"
        port = find_available_port("127.0.0.1", 49350)
        self.server, self.url = create_dashboard_server(
            host="127.0.0.1",
            port=port,
            auth_file=self.auth_file,
            base_url="https://example.test",
            store_dir=self.store_dir,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def test_cached_endpoint_reads_sqlite_without_network(self) -> None:
        UsageMonitor(self.store_dir / "usage-history.sqlite3").record_success(
            "account-1", _usage()
        )
        with (
            patch("src.dashboard_server.get_codex_usage") as usage_request,
            patch("src.dashboard_server.get_codex_reset_credits") as reset_request,
        ):
            data = json.load(urlopen(self.url + "/api/codex?cached=1", timeout=2))
        usage_request.assert_not_called()
        reset_request.assert_not_called()
        self.assertTrue(data["meta"]["initial_cache"])
        self.assertEqual(data["usage"]["rate_limit"]["primary_window"]["used_percent"], 7)

    def test_live_usage_and_reset_requests_run_in_parallel(self) -> None:
        barrier = threading.Barrier(2)

        def usage_request(**_: object) -> dict:
            barrier.wait(timeout=1)
            return _usage()

        def reset_request(**_: object) -> dict:
            barrier.wait(timeout=1)
            return {"credits": []}

        with (
            patch("src.dashboard_server.get_codex_usage", side_effect=usage_request),
            patch(
                "src.dashboard_server.get_codex_reset_credits",
                side_effect=reset_request,
            ),
        ):
            data = json.load(urlopen(self.url + "/api/codex", timeout=2))
        self.assertFalse(data["meta"]["cached"])
        self.assertEqual(data["reset_credits"], {"credits": []})


if __name__ == "__main__":
    unittest.main()
