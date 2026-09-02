from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.rate_windows import rate_limit_windows


DEFAULT_CACHE_TTL_SECONDS = 10 * 60
DEFAULT_HISTORY_DAYS = 30


class UsageMonitor:
    """Persist successful quota reads and derive consumption progress."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._initialize()

    def record_success(
        self,
        account_key: str,
        usage: dict[str, Any],
        *,
        observed_at: float | None = None,
    ) -> float:
        timestamp = float(time.time() if observed_at is None else observed_at)
        encoded = json.dumps(usage, ensure_ascii=False, separators=(",", ":"))
        samples = list(_usage_samples(usage))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO usage_cache(account_key, observed_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(account_key) DO UPDATE SET
                    observed_at = excluded.observed_at,
                    payload_json = excluded.payload_json
                """,
                (account_key, timestamp, encoded),
            )
            connection.executemany(
                """
                INSERT INTO usage_history(
                    account_key, observed_at, window_key, label,
                    duration_seconds, reset_at, used_percent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        account_key,
                        timestamp,
                        sample["window_key"],
                        sample["label"],
                        sample["duration_seconds"],
                        sample["reset_at"],
                        sample["used_percent"],
                    )
                    for sample in samples
                ],
            )
            connection.execute(
                "DELETE FROM usage_history WHERE observed_at < ?",
                (timestamp - DEFAULT_HISTORY_DAYS * 86400,),
            )
        return timestamp

    def cached_usage(
        self,
        account_key: str,
        *,
        max_age_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        now: float | None = None,
    ) -> tuple[dict[str, Any], float] | None:
        current = float(time.time() if now is None else now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT observed_at, payload_json FROM usage_cache WHERE account_key = ?",
                (account_key,),
            ).fetchone()
        if row is None or current - float(row[0]) > max_age_seconds:
            return None
        try:
            payload = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload, float(row[0])

    def progress(self, account_key: str, window_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            latest = connection.execute(
                """
                SELECT observed_at, label, used_percent, reset_at
                FROM usage_history
                WHERE account_key = ? AND window_key = ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (account_key, window_key),
            ).fetchone()
            if latest is None:
                return None
            previous = connection.execute(
                """
                SELECT used_percent FROM usage_history
                WHERE account_key = ? AND window_key = ? AND observed_at < ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (account_key, window_key, latest[0]),
            ).fetchone()
            hour_ago = float(latest[0]) - 3600
            hour_start = connection.execute(
                """
                SELECT used_percent FROM usage_history
                WHERE account_key = ? AND window_key = ? AND observed_at <= ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (account_key, window_key, hour_ago),
            ).fetchone()
            if hour_start is None:
                hour_start = connection.execute(
                    """
                    SELECT used_percent FROM usage_history
                    WHERE account_key = ? AND window_key = ? AND observed_at < ?
                    ORDER BY observed_at ASC LIMIT 1
                    """,
                    (account_key, window_key, latest[0]),
                ).fetchone()
        used = float(latest[2])
        return {
            "observed_at": float(latest[0]),
            "label": str(latest[1]),
            "used_percent": used,
            "reset_at": latest[3],
            "delta_previous": _non_negative_delta(used, previous[0] if previous else None),
            "delta_1h": _non_negative_delta(used, hour_start[0] if hour_start else None),
        }

    def history(self, account_key: str, *, hours: int = 24) -> list[dict[str, Any]]:
        cutoff = time.time() - max(1, hours) * 3600
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT observed_at, window_key, label, duration_seconds, reset_at, used_percent
                FROM usage_history
                WHERE account_key = ? AND observed_at >= ?
                ORDER BY observed_at ASC, window_key ASC
                """,
                (account_key, cutoff),
            ).fetchall()
        return [
            {
                "observed_at": float(row[0]),
                "window_key": str(row[1]),
                "label": str(row[2]),
                "duration_seconds": row[3],
                "reset_at": row[4],
                "used_percent": float(row[5]),
            }
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_cache (
                    account_key TEXT PRIMARY KEY,
                    observed_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_key TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    window_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    duration_seconds INTEGER,
                    reset_at REAL,
                    used_percent REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_history_lookup
                ON usage_history(account_key, window_key, observed_at);
                """
            )
            rows = connection.execute(
                "SELECT id, window_key, duration_seconds FROM usage_history"
            ).fetchall()
            for row_id, window_key, duration in rows:
                normalized = _stable_window_key(str(window_key), duration)
                if normalized != window_key:
                    connection.execute(
                        "UPDATE usage_history SET window_key = ? WHERE id = ?",
                        (normalized, row_id),
                    )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def primary_window_key(usage: dict[str, Any]) -> str | None:
    samples = list(_usage_samples(usage))
    return str(samples[0]["window_key"]) if samples else None


def usage_window_keys(usage: dict[str, Any]) -> set[str]:
    """Return only quota windows present in the current API response."""
    return {str(sample["window_key"]) for sample in _usage_samples(usage)}


def _usage_samples(usage: dict[str, Any]):
    sections: list[tuple[str, str, dict[str, Any]]] = [
        ("main", "主额度", usage.get("rate_limit") or {})
    ]
    for index, item in enumerate(usage.get("additional_rate_limits") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("limit_name") or item.get("metered_feature") or "附加额度")
        sections.append((f"additional:{index}:{title}", title, item.get("rate_limit") or {}))
    for section_key, section_title, rate_limit in sections:
        for position, (label, window) in enumerate(rate_limit_windows(rate_limit)):
            try:
                used = float(window["used_percent"])
            except (KeyError, TypeError, ValueError):
                continue
            duration = _optional_int(window.get("limit_window_seconds"))
            reset_at = _optional_float(window.get("reset_at"))
            yield {
                "window_key": f"{section_key}:{duration or f'unknown-{position}'}",
                "label": label if section_title == "主额度" else f"{section_title} · {label}",
                "duration_seconds": duration,
                "reset_at": reset_at,
                "used_percent": max(0.0, min(100.0, used)),
            }


def _non_negative_delta(current: float, previous: Any) -> float | None:
    try:
        delta = current - float(previous)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, delta), 2)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _stable_window_key(window_key: str, duration: Any) -> str:
    normalized_duration = _optional_int(duration)
    if normalized_duration is None:
        return window_key
    parts = window_key.rsplit(":", 2)
    if len(parts) == 3 and parts[1] in {"0", "1"} and parts[2] == str(normalized_duration):
        return f"{parts[0]}:{normalized_duration}"
    return window_key
