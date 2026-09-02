"""Helpers for naming and ordering ChatGPT rate-limit windows.

The API exposes the duration of each window on the window object itself via
``limit_window_seconds``.  Keeping the label calculation here deliberately
independent of the field name (``primary_window``/``secondary_window``)
avoids presenting a primary window as a five-hour window when an account has
an otherwise different quota shape.
"""

from __future__ import annotations

import math
from typing import Any


_HOUR = 60 * 60
_DAY = 24 * _HOUR
_WEEK = 7 * _DAY
_MONTH_MIN = 28 * _DAY
_MONTH_MAX = 31 * _DAY


def window_label(window: dict[str, Any] | None, fallback: str = "额度") -> str:
    """Return a Chinese label based only on ``window``'s duration.

    Known product windows receive concise labels: five hours, one week, and
    28--31 day windows are labelled ``5 小时``, ``周额度`` and ``月额度``.
    Other integral durations are rendered in hours, days, minutes, or seconds.
    A missing or invalid duration returns ``fallback``; in particular, this
    function never infers a duration from whether a window is primary or
    secondary.
    """

    if not isinstance(window, dict):
        return fallback

    seconds = _duration_seconds(window.get("limit_window_seconds"))
    if seconds is None:
        return fallback

    if seconds == 5 * _HOUR:
        return "5 小时"
    if seconds == _WEEK:
        return "周额度"
    if _MONTH_MIN <= seconds <= _MONTH_MAX:
        return "月额度"
    if seconds % _DAY == 0:
        return f"{seconds // _DAY} 天"
    if seconds % _HOUR == 0:
        return f"{seconds // _HOUR} 小时"
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def rate_limit_windows(rate_limit: dict[str, Any] | None) -> list[tuple[str, dict]]:
    """Extract non-empty primary/secondary windows in duration order.

    The returned tuple contains the dynamically calculated label and the
    original window dictionary.  Windows without a valid duration are kept
    (so callers can still display their other fields) and sorted after known
    durations.  Python's stable sort preserves primary-before-secondary order
    when both durations are missing or equal.
    """

    if not isinstance(rate_limit, dict):
        return []

    windows: list[tuple[int | None, int, str, dict]] = []
    for position, key in enumerate(("primary_window", "secondary_window")):
        window = rate_limit.get(key)
        if not isinstance(window, dict) or not window:
            continue
        duration = _duration_seconds(window.get("limit_window_seconds"))
        windows.append((duration, position, window_label(window), window))

    windows.sort(key=lambda item: (item[0] is None, item[0] if item[0] is not None else 0, item[1]))
    return [(label, window) for _, _, label, window in windows]


def usage_rate_limit_sections(usage: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize every usage quota section for all presentation layers."""
    raw_sections: list[tuple[str, str, dict[str, Any]]] = [
        ("main", "主额度", usage.get("rate_limit") or {})
    ]
    for index, item in enumerate(usage.get("additional_rate_limits") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("limit_name") or item.get("metered_feature") or "附加额度")
        raw_sections.append(
            (f"additional:{index}:{title}", title, item.get("rate_limit") or {})
        )
    return [
        {
            "key": key,
            "title": title,
            "limit_reached": bool(rate_limit.get("limit_reached")),
            "windows": [
                {"label": label, "window": window}
                for label, window in rate_limit_windows(rate_limit)
            ],
        }
        for key, title, rate_limit in raw_sections
    ]


def _duration_seconds(value: Any) -> int | None:
    """Normalize a finite positive integral duration to seconds."""

    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0 or not numeric.is_integer():
        return None
    return int(numeric)
