from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone


LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def resolve_window(window: str, bucket: str | None) -> tuple[int, int, str]:
    now = datetime.now(timezone.utc)
    end = now
    window = window.strip().lower()

    if ".." in window:
        left, right = window.split("..", 1)
        start = _parse_date_or_datetime(left)
        end = _parse_date_or_datetime(right)
    elif window == "5h":
        start = now - timedelta(hours=5)
    elif window == "today":
        local_now = now.astimezone(LOCAL_TZ)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=LOCAL_TZ)
        start = local_start.astimezone(timezone.utc)
    elif window == "week":
        local_now = now.astimezone(LOCAL_TZ)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=LOCAL_TZ)
        start = (local_start - timedelta(days=local_now.weekday())).astimezone(timezone.utc)
    elif window == "month":
        local_now = now.astimezone(LOCAL_TZ)
        local_start = datetime(local_now.year, local_now.month, 1, tzinfo=LOCAL_TZ)
        start = local_start.astimezone(timezone.utc)
    else:
        match = re.fullmatch(r"(\d+)([hd])", window)
        if not match:
            raise ValueError(f"不支持的时间窗口：{window}")
        amount = int(match.group(1))
        unit = match.group(2)
        start = now - (timedelta(hours=amount) if unit == "h" else timedelta(days=amount))

    if end <= start:
        raise ValueError("结束时间必须晚于开始时间")

    resolved_bucket = bucket or _default_bucket(start, end)
    return int(start.timestamp()), int(end.timestamp()), resolved_bucket


def _parse_date_or_datetime(value: str) -> datetime:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = datetime.fromisoformat(value).replace(tzinfo=LOCAL_TZ)
    else:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(timezone.utc)


def _default_bucket(start: datetime, end: datetime) -> str:
    seconds = (end - start).total_seconds()
    if seconds <= 6 * 60 * 60:
        return "1m"
    if seconds <= 7 * 24 * 60 * 60:
        return "1h"
    return "1d"
