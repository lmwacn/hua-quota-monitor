from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from src.codex_widget import (
    _account_title_suffix,
    _format_account_summary,
    _format_credit_menu_title,
    _format_limit_item,
    _format_plan_name,
    _format_reset_item,
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
        snapshot["usage_cached"] = True
        self.assertEqual(_account_title_suffix(snapshot), " · 额度 剩余 72% · 缓存")

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

    def test_account_title_includes_subscription_level(self) -> None:
        snapshot = {
            "usage": {
                "plan_type": "pro",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 30,
                        "limit_window_seconds": 604_800,
                    }
                },
            }
        }
        self.assertEqual(
            _account_title_suffix(snapshot), " · Pro · 周额度 剩余 70%"
        )
        self.assertEqual(_format_plan_name("plus"), "Plus")
        self.assertEqual(_format_plan_name("Custom Plan"), "Custom Plan")
        self.assertEqual(_format_plan_name(None), "")

    def test_account_reset_item_includes_local_date_and_time(self) -> None:
        reset_at = datetime(2026, 9, 16, 19).timestamp()
        self.assertEqual(
            _format_reset_item({"reset_at": reset_at}),
            "重置时间：9月16日 19:00 重置",
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

    def test_summary_merges_nickname_and_update_minute(self) -> None:
        updated = datetime(2026, 9, 1, 21, 1, 27)
        self.assertEqual(
            _format_account_summary("超哥", updated),
            "顶栏主账号：超哥（21:01）",
        )
        self.assertEqual(_format_account_summary("超哥"), "顶栏主账号：超哥")

    def test_reset_credit_count_is_merged_into_submenu_title(self) -> None:
        self.assertEqual(
            _format_credit_menu_title(0, [], True),
            "重置卡到期时间（0 张）",
        )
        self.assertEqual(
            _format_credit_menu_title(None, [{}, {}], True),
            "重置卡到期时间（2 张）",
        )
        self.assertEqual(
            _format_credit_menu_title(None, [], False),
            "重置卡到期时间（暂时无法获取）",
        )


if __name__ == "__main__":
    unittest.main()
