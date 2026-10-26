"""Shared quota menu content for the floating strip and Windows tray."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.codex_widget import (
    _account_title_suffix, _format_balance, _format_balance_usd,
    _format_credit_expiry, _format_credit_menu_title, _format_limit_item,
    _format_reset_item, _has_positive_balance,
)
from src.rate_windows import rate_limit_windows


@dataclass
class Entry:
    title: str = ""
    action: Callable | None = None
    children: list | None = None
    checked: bool | None = None


def details(result: dict | None) -> list[Entry]:
    if result is None:
        return [Entry("额度：等待刷新")]
    usage = result.get("usage")
    if usage is None:
        return [Entry("额度：查询失败"), Entry(str(result.get("error") or "未知错误")[:100])]
    rows = []
    if result.get("usage_cached"):
        rows.append(Entry("状态：缓存数据"))
    windows = rate_limit_windows(usage.get("rate_limit") or {})
    rows.extend(Entry(_format_limit_item(label, window)) for label, window in windows)
    rows.append(Entry(_format_reset_item(windows[0][1]) if windows else "额度：暂无"))
    balance = (usage.get("credits") or {}).get("balance")
    if _has_positive_balance(balance):
        rows.append(Entry(f"点数：{_format_balance(balance)}（余额：{_format_balance_usd(balance)}）"))
    return rows


def quota_entries(result: dict | None, profiles: list, results: dict, selected: str | None,
                  actions: dict, select: Callable, rename: Callable) -> list[Entry]:
    profile = result.get("profile") if result else None
    name = getattr(profile, "display_name", None) or (profile.get("display_name") if isinstance(profile, dict) else None) or "当前账号"
    updated = result.get("updated") if result else None
    suffix = f"（{updated:%H:%M}）" if updated else ""
    rows = [Entry(f"主账号：{name}{suffix}"), *details(result)]
    usage = result.get("usage") or {} if result else {}
    reset = result.get("reset") if result else None
    credits = (reset or {}).get("credits") or []
    count = (usage.get("rate_limit_reset_credits") or {}).get("available_count")
    credit_rows = [Entry(f"{c.get('title') or '重置卡'} · {_format_credit_expiry(str(c['expires_at']))}")
                   for c in credits if c.get("expires_at")]
    if not credit_rows:
        credit_rows = [Entry("暂无可用重置卡" if reset is not None else "重置卡详情暂不可用")]
    rows.extend([Entry(), Entry(_format_credit_menu_title(count, credits, reset is not None), children=credit_rows)])
    accounts = []
    for profile in profiles:
        children = details(results.get(profile.name)) + [Entry()]
        if profile.name == selected:
            children.append(Entry("当前主账号"))
        else:
            children.append(Entry("设为主账号", lambda name=profile.name: select(name)))
        children.append(Entry("重命名账号…", lambda name=profile.name: rename(name)))
        accounts.append(Entry(("✓ " if profile.name == selected else "") + profile.display_name +
                              _account_title_suffix(results.get(profile.name)), children=children))
    if not accounts:
        accounts.append(Entry("当前使用 Codex 登录账号"))
    accounts += [Entry(), Entry("添加当前账号…", actions['add']), Entry("从 auth.json 导入…", actions['import'])]
    rows += [Entry("账号监控", children=accounts), Entry(),
             Entry("刷新所有账号", actions['refresh']), Entry("打开额度趋势面板", actions['dashboard'])]
    return rows
