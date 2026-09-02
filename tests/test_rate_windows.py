from __future__ import annotations

import unittest

from src.rate_windows import rate_limit_windows, usage_rate_limit_sections, window_label


def test_window_label_uses_duration_for_known_windows() -> None:
    assert window_label({"limit_window_seconds": 18_000}) == "5 小时"
    assert window_label({"limit_window_seconds": 604_800}) == "周额度"
    assert window_label({"limit_window_seconds": 28 * 86_400}) == "月额度"
    assert window_label({"limit_window_seconds": 29 * 86_400}) == "月额度"
    assert window_label({"limit_window_seconds": 30 * 86_400}) == "月额度"
    assert window_label({"limit_window_seconds": 31 * 86_400}) == "月额度"


def test_window_label_uses_reasonable_labels_for_other_durations() -> None:
    assert window_label({"limit_window_seconds": 3 * 86_400}) == "3 天"
    assert window_label({"limit_window_seconds": 2 * 3_600}) == "2 小时"
    assert window_label({"limit_window_seconds": 90 * 60}) == "90 分钟"
    assert window_label({"limit_window_seconds": 17}) == "17 秒"


def test_window_label_falls_back_for_missing_or_invalid_duration() -> None:
    fallback = "未知额度"
    assert window_label({}, fallback) == fallback
    assert window_label(None, fallback) == fallback
    assert window_label({"limit_window_seconds": None}, fallback) == fallback
    assert window_label({"limit_window_seconds": "not-a-duration"}, fallback) == fallback
    assert window_label({"limit_window_seconds": 0}, fallback) == fallback


def test_window_label_does_not_guess_from_window_role() -> None:
    assert window_label({"role": "primary", "used_percent": 42}, "未提供") == "未提供"
    assert window_label({"role": "secondary", "used_percent": 42}, "未提供") == "未提供"


def test_rate_limit_windows_extracts_non_empty_windows_and_sorts_by_duration() -> None:
    primary = {"limit_window_seconds": 604_800, "used_percent": 20}
    secondary = {"limit_window_seconds": 18_000, "used_percent": 30}
    rate_limit = {
        "primary_window": primary,
        "secondary_window": secondary,
        "unrelated": {"limit_window_seconds": 1},
    }

    assert rate_limit_windows(rate_limit) == [("5 小时", secondary), ("周额度", primary)]


def test_rate_limit_windows_skips_empty_and_keeps_unknown_duration_last() -> None:
    primary = {"used_percent": 20}
    secondary = {"limit_window_seconds": 30 * 86_400, "used_percent": 30}
    assert rate_limit_windows(
        {"primary_window": primary, "secondary_window": secondary}
    ) == [("月额度", secondary), ("额度", primary)]
    assert rate_limit_windows({"primary_window": {}, "secondary_window": None}) == []
    assert rate_limit_windows(None) == []


def test_usage_sections_share_normalized_labels_with_all_interfaces() -> None:
    usage = {
        "rate_limit": {
            "primary_window": {
                "limit_window_seconds": 604_800,
                "used_percent": 7,
            }
        },
        "additional_rate_limits": [
            {
                "limit_name": "Spark",
                "rate_limit": {
                    "primary_window": {
                        "limit_window_seconds": 18_000,
                        "used_percent": 3,
                    }
                },
            }
        ],
    }
    sections = usage_rate_limit_sections(usage)
    assert [(item["key"], item["title"]) for item in sections] == [
        ("main", "主额度"),
        ("additional:0:Spark", "Spark"),
    ]
    assert sections[0]["windows"][0]["label"] == "周额度"
    assert sections[1]["windows"][0]["label"] == "5 小时"


class RateWindowUnittestTests(unittest.TestCase):
    """Expose the assertions to the project's unittest discovery command."""

    def test_known_labels(self) -> None:
        test_window_label_uses_duration_for_known_windows()

    def test_other_labels(self) -> None:
        test_window_label_uses_reasonable_labels_for_other_durations()

    def test_invalid_duration_fallback(self) -> None:
        test_window_label_falls_back_for_missing_or_invalid_duration()
        test_window_label_does_not_guess_from_window_role()

    def test_window_extraction_and_sorting(self) -> None:
        test_rate_limit_windows_extracts_non_empty_windows_and_sorts_by_duration()

    def test_empty_and_unknown_windows(self) -> None:
        test_rate_limit_windows_skips_empty_and_keeps_unknown_duration_last()

    def test_usage_sections(self) -> None:
        test_usage_sections_share_normalized_labels_with_all_interfaces()
