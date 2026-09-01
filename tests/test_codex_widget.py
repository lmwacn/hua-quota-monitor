from __future__ import annotations

import unittest
from pathlib import Path

from src.codex_widget import (
    _account_title_suffix,
    _format_limit_item,
    _is_valid_display_name,
    _is_valid_account_name,
    _profile_display_name,
    _suggest_display_name,
    _suggest_account_name,
)


class CodexWidgetFormattingTests(unittest.TestCase):
    def test_missing_usage_never_reports_full_remaining_quota(self) -> None:
        self.assertEqual(_account_title_suffix(None), " · 等待刷新")
        self.assertEqual(
            _account_title_suffix({"usage": {"rate_limit": {"primary_window": {}}}}),
            " · 额度暂无",
        )
        self.assertEqual(_format_limit_item("5 小时", {}), "5 小时：暂无")

    def test_remaining_quota_is_formatted_from_used_percent(self) -> None:
        snapshot = {
            "usage": {
                "rate_limit": {"primary_window": {"used_percent": 27.6}}
            }
        }
        self.assertEqual(_account_title_suffix(snapshot), " · 额度 剩余 72%")
        self.assertEqual(
            _format_limit_item("5 小时", {"used_percent": 27.6}),
            "5 小时：剩余 72%",
        )

    def test_account_title_uses_actual_window_duration(self) -> None:
        def snapshot(seconds: int) -> dict:
            return {
                "usage": {
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 53,
                            "limit_window_seconds": seconds,
                        }
                    }
                }
            }

        self.assertEqual(_account_title_suffix(snapshot(18_000)), " · 5 小时 剩余 47%")
        self.assertEqual(_account_title_suffix(snapshot(604_800)), " · 周额度 剩余 47%")
        self.assertEqual(
            _account_title_suffix(snapshot(2_592_000)), " · 月额度 剩余 47%"
        )

    def test_account_alias_suggestion_is_safe_and_unique(self) -> None:
        self.assertTrue(_is_valid_account_name("work-2"))
        self.assertFalse(_is_valid_account_name("工作账号"))
        self.assertFalse(_is_valid_account_name("../escape"))
        self.assertEqual(
            _suggest_account_name(Path("/tmp/work/auth.json"), {"work"}),
            "work-2",
        )
        self.assertEqual(
            _suggest_account_name(Path("/tmp/账号/auth.json"), set()),
            "account",
        )

    def test_display_name_accepts_chinese_but_rejects_controls(self) -> None:
        self.assertTrue(_is_valid_display_name("个人 Pro"))
        self.assertFalse(_is_valid_display_name(""))
        self.assertFalse(_is_valid_display_name("工作\n账号"))
        self.assertEqual(
            _profile_display_name({"name": "work", "display_name": "工作账号"}),
            "工作账号",
        )
        self.assertEqual(_profile_display_name({"name": "work"}), "work")
        self.assertEqual(_suggest_display_name(Path("/tmp/个人/auth.json")), "个人")


if __name__ == "__main__":
    unittest.main()
