from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from shlex import quote
from threading import Thread
from typing import Any

from src.codex_usage import get_codex_reset_credits, get_codex_usage


def run_menubar(
    *,
    auth_file: Path,
    base_url: str,
    interval: int,
    width: int,
    height: int,
    alpha: float,
) -> int:
    """Run the native macOS menu-bar quota item."""
    try:
        import AppKit
        import objc
        from Foundation import NSObject, NSTimer
    except ImportError:
        print("菜单栏模式需要 PyObjC，请使用项目 .venv 启动。")
        return 2

    class MenubarController(NSObject):
        def initWithAuthFile_baseURL_interval_(self, auth_path: str, endpoint: str, seconds: int):
            self = self.init()
            if self is None:
                return None
            self.auth_path = Path(auth_path)
            self.base_url = endpoint
            self.interval = max(5, int(seconds))
            self.refreshing = False
            return self

        def start(self) -> None:
            app = AppKit.NSApplication.sharedApplication()
            app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
                AppKit.NSVariableStatusItemLength
            )
            button = self.status_item.button()
            icon = AppKit.NSImage.alloc().initWithContentsOfFile_(_menubar_icon_path())
            if icon is not None:
                icon.setSize_(AppKit.NSMakeSize(18, 18))
                icon.setTemplate_(True)
                button.setImage_(icon)
            button.setTitle_("…")

            self.menu = AppKit.NSMenu.alloc().init()
            self.menu_items = {}
            for key, title in (
                ("main", "主额度：刷新中"),
                ("main_reset", "重置时间：刷新中"),
                ("balance", "点数：刷新中"),
                ("credits", "可用重置卡：刷新中"),
                ("updated", "最近更新：刷新中"),
            ):
                item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
                item.setTarget_(self)
                item.setAction_("noop:")
                self.menu.addItem_(item)
                self.menu_items[key] = item

            self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self.credits_submenu = AppKit.NSMenu.alloc().init()
            credits_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "重置卡到期时间", None, ""
            )
            credits_item.setSubmenu_(self.credits_submenu)
            self.menu.addItem_(credits_item)

            refresh_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("刷新", "refresh:", "")
            refresh_item.setTarget_(self)
            self.menu.addItem_(refresh_item)
            quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("退出", "quit:", "")
            quit_item.setTarget_(self)
            self.menu.addItem_(quit_item)
            self.status_item.setMenu_(self.menu)

            self.refresh_(None)
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.interval, self, "refresh:", None, True
            )
            app.run()

        def refresh_(self, sender: Any) -> None:
            if self.refreshing:
                return
            self.refreshing = True
            Thread(target=self._fetch_refresh, name="codex-usage-refresh", daemon=True).start()

        def _fetch_refresh(self) -> None:
            usage = None
            reset = None
            errors = []
            try:
                usage = get_codex_usage(auth_path=self.auth_path, base_url=self.base_url)
            except Exception as exc:
                errors.append(f"额度接口：{exc}")
            try:
                reset = get_codex_reset_credits(auth_path=self.auth_path, base_url=self.base_url)
            except Exception as exc:
                errors.append(f"重置卡接口：{exc}")
            result = (usage, reset, "；".join(errors) if errors else None)
            self.performSelectorOnMainThread_withObject_waitUntilDone_("finishRefresh:", result, False)

        def finishRefresh_(self, result: tuple[Any, Any, str | None]) -> None:
            self.refreshing = False
            usage, reset, error = result
            if error:
                print(f"菜单栏刷新部分失败：{error}")
            if usage is None:
                self.menu_items["main"].setTitle_("主额度：查询失败")
                self.menu_items["main_reset"].setTitle_("重置时间：查询失败")
                self.menu_items["balance"].setTitle_("点数：查询失败")
                self.menu_items["credits"].setTitle_("可用重置卡：查询失败")
                self.menu_items["updated"].setTitle_("最近更新：失败")
                self.status_item.button().setTitle_("!")
                return

            try:
                primary = (usage.get("rate_limit") or {}).get("primary_window") or {}
                credits = reset.get("credits") or [] if reset else []
                count = usage.get("rate_limit_reset_credits", {}).get("available_count")

                self.menu_items["main"].setTitle_(_format_limit_item("主额度", primary))
                self.menu_items["main_reset"].setTitle_(_format_reset_item(primary))
                balance = (usage.get("credits") or {}).get("balance")
                has_balance = _has_positive_balance(balance)
                self.menu_items["balance"].setHidden_(not has_balance)
                if has_balance:
                    self.menu_items["balance"].setTitle_(
                        f"点数：{_format_balance(balance)}（余额：{_format_balance_usd(balance)}）"
                    )
                if reset is None:
                    self.menu_items["credits"].setTitle_("可用重置卡：暂时无法获取")
                    self.update_credit_submenu([])
                else:
                    credit_count = count if count is not None else len(credits)
                    self.menu_items["credits"].setTitle_(f"可用重置卡：{credit_count} 张")
                    self.update_credit_submenu(credits)
                updated = datetime.now().strftime("%H:%M:%S")
                self.menu_items["updated"].setTitle_(f"最近更新：{updated}")

                remaining = max(0, 100 - _to_percent(primary.get("used_percent"))) if primary else 0
                status_title = f"{remaining}%"
                if has_balance:
                    status_title += f" · {_format_balance(balance)}（{_format_balance_usd(balance)}）"
                self.status_item.button().setTitle_(status_title)
            except (AttributeError, TypeError, ValueError) as exc:
                self.menu_items["main"].setTitle_("主额度：查询失败")
                self.menu_items["main_reset"].setTitle_("重置时间：查询失败")
                self.menu_items["balance"].setTitle_("点数：查询失败")
                self.menu_items["credits"].setTitle_("可用重置卡：查询失败")
                self.menu_items["updated"].setTitle_("最近更新：失败")
                self.status_item.button().setTitle_("!")
                print(f"菜单栏刷新失败：{exc}")

        def update_credit_submenu(self, credits: list[dict[str, Any]]) -> None:
            while self.credits_submenu.numberOfItems() > 0:
                self.credits_submenu.removeItemAtIndex_(0)
            visible = [credit for credit in credits if credit.get("expires_at")]
            visible.sort(key=lambda credit: str(credit.get("expires_at")))
            if not visible:
                self._add_info_item("暂无可用重置卡")
                return
            for index, credit in enumerate(visible, start=1):
                title = credit.get("title") or "Full reset"
                expiry = _format_credit_expiry(str(credit.get("expires_at")))
                self._add_info_item(f"{index}. {title} · {expiry}")

        def _add_info_item(self, title: str) -> None:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            item.setTarget_(self)
            item.setAction_("noop:")
            self.credits_submenu.addItem_(item)

        def noop_(self, sender: Any) -> None:
            pass

        def quit_(self, sender: Any) -> None:
            AppKit.NSApplication.sharedApplication().terminate_(self)

    controller = MenubarController.alloc().initWithAuthFile_baseURL_interval_(
        str(auth_file), base_url, interval
    )
    controller.start()
    return 0


def create_menubar_shortcut(*, project_root: Path, interval: int, name: str = "打开Codex顶栏") -> Path:
    shortcut = Path("~/Desktop").expanduser() / f"{name}.command"
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    python_bin = project_root / ".venv" / "bin" / "python"
    python_command = quote(python_bin.as_posix()) if python_bin.exists() else "python"
    main_path = quote((project_root / "main.py").as_posix())
    script = f"""#!/bin/zsh
cd {quote(project_root.as_posix())}
launchctl remove com.xiaomeng.codexmenubar 2>/dev/null
launchctl submit -l com.xiaomeng.codexmenubar -- {python_command} {main_path} menubar --interval {int(interval)}
"""
    shortcut.write_text(script, encoding="utf-8")
    shortcut.chmod(0o755)
    return shortcut


def _menubar_icon_path() -> str:
    svg_path = Path(__file__).resolve().parents[1] / "assets" / "codex-menubar.svg"
    if svg_path.exists():
        return str(svg_path)
    return "/Applications/ChatGPT.app/Contents/Resources/icon-codex-dark-color.png"


def _to_percent(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _format_balance(value: Any) -> str:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{amount:,.2f} 点"
    except (InvalidOperation, TypeError, ValueError):
        return "暂无"


def _format_balance_usd(value: Any) -> str:
    try:
        amount = (Decimal(str(value)) / Decimal("25")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return f"${amount:,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return "暂无"


def _has_positive_balance(value: Any) -> bool:
    try:
        return Decimal(str(value)) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _format_limit_item(label: str, window: dict[str, Any]) -> str:
    if not window:
        return f"{label}：暂无"
    remaining = max(0, 100 - _to_percent(window.get("used_percent")))
    result = f"{label}：剩余 {remaining}%"
    reset_at = window.get("reset_at")
    if reset_at is not None:
        result += f" · {_format_remaining(float(reset_at)).removeprefix('剩余 ')}"
    return result


def _format_reset_item(window: dict[str, Any]) -> str:
    reset_at = window.get("reset_at") if window else None
    if reset_at is None:
        return "重置时间：暂无"
    formatted = _format_reset_at(reset_at)
    date_time, _, remaining = formatted.partition("，")
    return f"重置时间：{date_time or '未知'}"


def _format_reset_at(value: Any) -> str:
    try:
        timestamp = float(value)
        return _format_expiry_timestamp(timestamp, "重置")
    except (TypeError, ValueError, OverflowError, OSError):
        return "时间未知"


def _format_credit_expiry(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return _format_expiry_timestamp(parsed.timestamp(), "到期")
    except (TypeError, ValueError):
        return value[:16].replace("T", " ")


def _format_expiry_timestamp(timestamp: float, label: str) -> str:
    local_time = datetime.fromtimestamp(timestamp)
    return f"{local_time.strftime('%-m月%-d日 %H:%M')} {label}，{_format_remaining(timestamp)}"


def _format_remaining(timestamp: float) -> str:
    seconds = max(0, int(timestamp - datetime.now().timestamp()))
    if seconds == 0:
        return "已到期"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"剩余 {days}天{hours}小时"
    if hours:
        return f"剩余 {hours}小时{minutes}分钟"
    return f"剩余 {max(1, minutes)}分钟"
