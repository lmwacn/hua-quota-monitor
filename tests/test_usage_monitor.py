from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from src.usage_monitor import UsageMonitor, primary_window_key


def usage(used: float, *, reset_at: int = 2_000_000_000) -> dict:
    return {
        "account_id": "account-1",
        "rate_limit": {
            "primary_window": {
                "used_percent": used,
                "limit_window_seconds": 18_000,
                "reset_at": reset_at,
            }
        },
    }


class UsageMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.monitor = UsageMonitor(Path(self.temp_dir.name) / "history.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cache_expires_ten_minutes_after_real_success(self) -> None:
        self.monitor.record_success("account-1", usage(12), observed_at=1000)
        cached = self.monitor.cached_usage("account-1", now=1599)
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0]["rate_limit"]["primary_window"]["used_percent"], 12)
        self.assertEqual(cached[1], 1000)
        self.assertIsNone(self.monitor.cached_usage("account-1", now=1601))

    def test_progress_records_positive_consumption(self) -> None:
        self.monitor.record_success("account-1", usage(12), observed_at=1000)
        self.monitor.record_success("account-1", usage(17.5), observed_at=1060)
        progress = self.monitor.progress("account-1", primary_window_key(usage(17.5)))
        self.assertEqual(progress["used_percent"], 17.5)
        self.assertEqual(progress["delta_previous"], 5.5)
        self.assertEqual(progress["delta_1h"], 5.5)

    def test_reset_does_not_report_negative_consumption(self) -> None:
        self.monitor.record_success("account-1", usage(90), observed_at=1000)
        self.monitor.record_success("account-1", usage(2, reset_at=2_100_000_000), observed_at=1060)
        progress = self.monitor.progress("account-1", primary_window_key(usage(2)))
        self.assertEqual(progress["delta_previous"], 0)

    def test_every_success_and_every_account_are_kept_separately(self) -> None:
        now = time.time()
        self.monitor.record_success("account-1", usage(20), observed_at=now - 60)
        self.monitor.record_success("account-1", usage(20), observed_at=now)
        self.monitor.record_success("account-2", usage(70), observed_at=now)
        account_1 = self.monitor.history("account-1")
        account_2 = self.monitor.history("account-2")
        self.assertEqual([row["used_percent"] for row in account_1], [20, 20])
        self.assertEqual([row["used_percent"] for row in account_2], [70])


if __name__ == "__main__":
    unittest.main()
