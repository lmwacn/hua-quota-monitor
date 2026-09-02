from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from src.account_store import AccountStore
from src.codex_usage import (
    CodexUsageError,
    get_codex_reset_credits,
    get_codex_usage,
    load_codex_auth,
)
from src.usage_monitor import UsageMonitor, usage_window_keys


class QuotaService:
    """Shared account, network, cache, and history behavior for every UI."""

    def __init__(
        self,
        *,
        auth_file: Path,
        base_url: str,
        monitor: UsageMonitor,
        account_store: AccountStore | None = None,
    ) -> None:
        self.auth_file = Path(auth_file)
        self.base_url = base_url
        self.monitor = monitor
        self.account_store = account_store

    def profiles(
        self, *, sync_canonical: bool = False
    ) -> tuple[list[Any], str | None, str | None]:
        if self.account_store is None:
            profile = self.canonical_profile()
            return [profile], str(profile["name"]), str(profile["name"])
        if sync_canonical:
            self.account_store.sync_canonical()
        profiles = list(self.account_store.list_accounts())
        current = self.account_store.get_current()
        selected = _profile_value(current, "name") if current else None
        active = self.account_store.detect_active_name()
        return profiles, selected, active

    def resolve_profile(self, account_name: str | None) -> Any:
        if account_name and self.account_store is not None:
            return self.account_store.get_account(account_name)
        return self.canonical_profile()

    def canonical_profile(self) -> dict[str, Any]:
        try:
            auth = load_codex_auth(self.auth_file)
            account_id = auth.account_id
        except CodexUsageError:
            account_id = None
        return {
            "name": "当前账号",
            "display_name": "当前账号",
            "auth_path": self.auth_file,
            "account_id": account_id,
            "is_active": True,
            "error": None,
        }

    def fetch_profile(
        self, profile: Any, *, active_name: str | None = None
    ) -> dict[str, Any]:
        context = self._context(profile, active_name=active_name)
        if context["error"]:
            return self._empty_result(profile, context)

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="quota-api") as pool:
            usage_future = pool.submit(
                get_codex_usage,
                auth_path=context["auth_path"],
                base_url=self.base_url,
            )
            reset_future = pool.submit(
                get_codex_reset_credits,
                auth_path=context["auth_path"],
                base_url=self.base_url,
            )
            usage, usage_error, observed_at, cached, transient = self._usage_result(
                usage_future, context["account_key"]
            )
            try:
                reset = reset_future.result()
                reset_error = None
            except Exception as exc:
                reset = None
                reset_error = f"重置卡：{exc}"

        errors = [error for error in (usage_error, reset_error) if error]
        return {
            "profile": profile,
            "usage": usage,
            "reset": reset,
            "error": "；".join(errors) if errors else None,
            "usage_error": usage_error,
            "reset_error": reset_error,
            "updated": datetime.now(),
            "usage_cached": cached,
            "usage_error_transient": transient,
            "usage_observed_at": observed_at,
            "account_key": context["account_key"],
        }

    def cached_profile(
        self, profile: Any, *, active_name: str | None = None
    ) -> dict[str, Any] | None:
        context = self._context(profile, active_name=active_name)
        if context["error"]:
            return None
        cached = self.monitor.cached_usage(context["account_key"])
        if cached is None:
            return None
        usage, observed_at = cached
        return {
            "profile": profile,
            "usage": usage,
            "reset": None,
            "error": None,
            "usage_error": None,
            "reset_error": None,
            "updated": datetime.now(),
            "usage_cached": True,
            "usage_error_transient": False,
            "usage_observed_at": observed_at,
            "account_key": context["account_key"],
        }

    def current_history(
        self, result: dict[str, Any], *, hours: int = 24
    ) -> list[dict[str, Any]]:
        usage = result.get("usage") or {}
        active_keys = usage_window_keys(usage)
        return [
            row
            for row in self.monitor.history(result["account_key"], hours=hours)
            if row["window_key"] in active_keys
        ]

    def _context(self, profile: Any, *, active_name: str | None) -> dict[str, Any]:
        name = str(_profile_value(profile, "name") or "未命名账号")
        account_key = str(_profile_value(profile, "account_id") or name)
        is_active = bool(_profile_value(profile, "is_active")) or name == active_name
        auth_path = (
            self.auth_file
            if is_active
            else Path(_profile_value(profile, "auth_path"))
        )
        return {
            "name": name,
            "account_key": account_key,
            "auth_path": auth_path,
            "error": _profile_value(profile, "error"),
        }

    def _usage_result(
        self, future: Any, account_key: str
    ) -> tuple[dict[str, Any] | None, str | None, float | None, bool, bool]:
        try:
            usage = future.result()
        except Exception as exc:
            cached = self.monitor.cached_usage(account_key)
            transient = isinstance(exc, CodexUsageError) and exc.transient
            if cached is None:
                return None, f"额度：{exc}", None, False, transient
            usage, observed_at = cached
            return usage, f"额度：{exc}", observed_at, True, transient
        observed_at = self.monitor.record_success(account_key, usage)
        return usage, None, observed_at, False, False

    @staticmethod
    def _empty_result(profile: Any, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "profile": profile,
            "usage": None,
            "reset": None,
            "error": str(context["error"]),
            "usage_error": str(context["error"]),
            "reset_error": None,
            "updated": datetime.now(),
            "usage_cached": False,
            "usage_error_transient": False,
            "usage_observed_at": None,
            "account_key": context["account_key"],
        }


def _profile_value(profile: Any, key: str) -> Any:
    if isinstance(profile, dict):
        return profile.get(key)
    return getattr(profile, key, None)
