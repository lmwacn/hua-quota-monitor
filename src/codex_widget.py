from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re
from shlex import quote
import tempfile
from threading import Thread
from time import monotonic
from typing import Any

from src.codex_usage import get_codex_reset_credits, get_codex_usage
from src.web_auth import login_with_chatgpt


def run_menubar(
    *,
    auth_file: Path,
    base_url: str,
    interval: int,
    width: int,
    height: int,
    alpha: float,
    account_store: Any | None = None,
) -> int:
    """Run the native macOS menu-bar quota item."""
    return _run_menubar_impl(
        auth_file=auth_file,
        base_url=base_url,
        interval=interval,
        account_store=account_store,
    )


def _run_menubar_impl(
    *, auth_file: Path, base_url: str, interval: int, account_store: Any | None
) -> int:
    try:
        import AppKit
        import objc
        from Foundation import NSObject, NSTimer
    except ImportError:
        print("菜单栏模式需要 PyObjC，请使用项目 .venv 启动。")
        return 2

    class MultiAccountController(NSObject):
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
            self.summary_items = {}
            for key, title in (
                ("account", "顶栏主账号：加载中"),
                ("main", "主额度：刷新中"),
                ("main_reset", "重置时间：刷新中"),
                ("balance", "点数：刷新中"),
                ("credits", "可用重置卡：刷新中"),
                ("updated", "最近更新：刷新中"),
            ):
                self.summary_items[key] = self._info(self.menu, title)
            self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self.credit_menu = AppKit.NSMenu.alloc().init()
            credit_root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "重置卡到期时间", None, ""
            )
            credit_root.setSubmenu_(self.credit_menu)
            self.menu.addItem_(credit_root)
            self.accounts_menu = AppKit.NSMenu.alloc().init()
            accounts_root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "账号监控", None, ""
            )
            accounts_root.setSubmenu_(self.accounts_menu)
            self.menu.addItem_(accounts_root)
            self._info(self.accounts_menu, "账号列表：加载中")
            self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self._action(self.menu, "刷新当前账号", "refreshCurrent:")
            self._action(self.menu, "刷新全部账号", "refreshAll:")
            self.manage_accounts_menu = AppKit.NSMenu.alloc().init()
            manage_accounts_root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "管理账号", None, ""
            )
            manage_accounts_root.setSubmenu_(self.manage_accounts_menu)
            self.menu.addItem_(manage_accounts_root)
            self._action(
                self.manage_accounts_menu,
                "添加当前 ChatGPT 账号…",
                "addCurrentAccount:",
            )
            self._action(
                self.manage_accounts_menu,
                "通过 ChatGPT 网页授权添加账号…",
                "webAuthorizeAccount:",
            )
            self._action(
                self.manage_accounts_menu,
                "从 auth.json 导入…",
                "importAuthFile:",
            )
            self._action(
                self.manage_accounts_menu,
                "更新当前账号凭据",
                "snapshotCurrent:",
            )
            self.manage_accounts_menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self._action(
                self.manage_accounts_menu,
                "打开账号存储目录",
                "openAccountStore:",
            )
            self._action(self.menu, "退出", "quit:")
            self.status_item.setMenu_(self.menu)
            self.refreshAll_(None)
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.interval, self, "automaticRefresh:", None, True
            )
            app.run()

        @objc.python_method
        def _info(self, menu: Any, title: str) -> Any:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            item.setTarget_(self)
            item.setAction_("noop:")
            menu.addItem_(item)
            return item

        @objc.python_method
        def _action(self, menu: Any, title: str, selector: str, value: Any = None) -> Any:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
            item.setTarget_(self)
            if value is not None:
                item.setRepresentedObject_(value)
            menu.addItem_(item)
            return item

        def automaticRefresh_(self, sender: Any) -> None:
            self._start_refresh(monotonic() - self.last_all_refresh >= self.all_interval)

        def refreshCurrent_(self, sender: Any) -> None:
            self._start_refresh(False)

        def refreshAll_(self, sender: Any) -> None:
            self._start_refresh(True)

        @objc.python_method
        def _start_refresh(self, all_accounts: bool) -> None:
            if self.refreshing:
                if all_accounts:
                    self.pending_refresh_all = True
                return
            if all_accounts:
                self.pending_refresh_all = False
            self.refreshing = True
            Thread(
                target=self._fetch,
                args=(all_accounts,),
                name="codex-accounts-refresh",
                daemon=True,
            ).start()

        @objc.python_method
        def _profiles(self) -> tuple[list[Any], str | None, str | None]:
            if self.account_store is None:
                return (
                    [{"name": "当前账号", "auth_path": self.auth_file}],
                    "当前账号",
                    "当前账号",
                )
            profiles = list(self.account_store.list_accounts())
            current = self.account_store.get_current()
            selected = _profile_value(current, "name") if current else None
            active = self.account_store.detect_active_name()
            return profiles, selected, active

        @objc.python_method
        def _fetch_profile(self, profile: Any, active_name: str | None) -> dict[str, Any]:
            name = str(_profile_value(profile, "name") or "未命名账号")
            # ChatGPT refreshes only the canonical auth cache. Read it for the
            # active account so monitoring does not use a stale imported token.
            path = (
                self.auth_file
                if name == active_name
                else Path(_profile_value(profile, "auth_path"))
            )
            usage = reset = None
            errors = []
            profile_error = _profile_value(profile, "error")
            if profile_error:
                return {
                    "profile": profile,
                    "usage": None,
                    "reset": None,
                    "error": str(profile_error),
                    "updated": datetime.now(),
                }
            try:
                usage = get_codex_usage(auth_path=path, base_url=self.base_url)
            except Exception as exc:
                errors.append(f"额度：{exc}")
            try:
                reset = get_codex_reset_credits(auth_path=path, base_url=self.base_url)
            except Exception as exc:
                errors.append(f"重置卡：{exc}")
            return {
                "profile": profile,
                "usage": usage,
                "reset": reset,
                "error": "；".join(errors) if errors else None,
                "updated": datetime.now(),
            }

        @objc.python_method
        def _fetch(self, all_accounts: bool) -> None:
            try:
                profiles, selected, active = self._profiles()
                targets = profiles if all_accounts else [
                    p for p in profiles if str(_profile_value(p, "name")) == selected
                ]
                if not targets and profiles:
                    targets, selected = [profiles[0]], str(_profile_value(profiles[0], "name"))
                fetched = {}
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix="codex-account") as pool:
                    futures = {
                        pool.submit(self._fetch_profile, profile, active): profile
                        for profile in targets
                    }
                    for future in as_completed(futures):
                        profile = futures[future]
                        name = str(_profile_value(profile, "name") or "未命名账号")
                        try:
                            fetched[name] = future.result()
                        except Exception as exc:
                            fetched[name] = {
                                "profile": profile, "usage": None, "reset": None,
                                "error": str(exc), "updated": datetime.now(),
                            }
                payload = (profiles, selected, active, fetched, all_accounts, None)
            except Exception as exc:
                payload = ([], None, None, {}, all_accounts, str(exc))
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "finishRefresh:", payload, False
            )

        def finishRefresh_(self, payload: tuple[Any, ...]) -> None:
            self.refreshing = False
            profiles, selected, active, fetched, all_accounts, fatal = payload
            if fatal:
                print(f"菜单栏刷新失败：{fatal}")
                self._set_failed_summary()
                self._run_pending_refresh()
                return
            self.selected_name = selected
            self.active_name = active
            selected_profile = next(
                (
                    profile
                    for profile in profiles
                    if str(_profile_value(profile, "name")) == selected
                ),
                None,
            )
            selected_display_name = (
                _profile_display_name(selected_profile) if selected_profile else None
            )
            self.summary_items["account"].setTitle_(
                f"顶栏主账号：{selected_display_name or '未选择'}"
            )
            self.results.update(fetched)
            if all_accounts:
                names = {str(_profile_value(p, "name")) for p in profiles}
                self.results = {k: v for k, v in self.results.items() if k in names}
                self.last_all_refresh = monotonic()
            self._build_accounts_menu(profiles)
            current = self.results.get(selected) if selected else None
            if current is None or current["usage"] is None:
                self._set_failed_summary()
            else:
                self._set_summary(selected_display_name or selected, current)
            self._run_pending_refresh()

        @objc.python_method
        def _run_pending_refresh(self) -> None:
            if not self.pending_refresh_all:
                return
            self.pending_refresh_all = False
            self._start_refresh(True)

        @objc.python_method
        def _set_failed_summary(self) -> None:
            for key in ("main", "main_reset", "balance", "credits"):
                self.summary_items[key].setTitle_(f"{self.summary_items[key].title().split('：')[0]}：查询失败")
            self.summary_items["updated"].setTitle_("最近更新：失败")
            self.status_item.button().setTitle_("!")

        @objc.python_method
        def _set_summary(self, name: str, item: dict[str, Any]) -> None:
            usage, reset = item["usage"], item["reset"]
            primary = (usage.get("rate_limit") or {}).get("primary_window") or {}
            self.summary_items["account"].setTitle_(f"顶栏主账号：{name}")
            self.summary_items["main"].setTitle_(_format_limit_item("主额度", primary))
            self.summary_items["main_reset"].setTitle_(_format_reset_item(primary))
            balance = (usage.get("credits") or {}).get("balance")
            positive = _has_positive_balance(balance)
            self.summary_items["balance"].setHidden_(not positive)
            if positive:
                self.summary_items["balance"].setTitle_(
                    f"点数：{_format_balance(balance)}（余额：{_format_balance_usd(balance)}）"
                )
            credits = reset.get("credits") or [] if reset else []
            count = (usage.get("rate_limit_reset_credits") or {}).get("available_count")
            if reset is None:
                self.summary_items["credits"].setTitle_("可用重置卡：暂时无法获取")
            else:
                self.summary_items["credits"].setTitle_(
                    f"可用重置卡：{count if count is not None else len(credits)} 张"
                )
            self._build_credit_menu(credits)
            self.summary_items["updated"].setTitle_(
                f"最近更新：{item['updated'].strftime('%H:%M:%S')}"
            )
            used_percent = primary.get("used_percent")
            title = "—" if used_percent is None else f"{max(0, 100 - _to_percent(used_percent))}%"
            if positive:
                title += f" · {_format_balance(balance)}（{_format_balance_usd(balance)}）"
            self.status_item.button().setTitle_(title)

        @objc.python_method
        def _build_credit_menu(self, credits: list[dict[str, Any]]) -> None:
            self.credit_menu.removeAllItems()
            visible = sorted(
                (c for c in credits if c.get("expires_at")),
                key=lambda c: str(c.get("expires_at")),
            )
            if not visible:
                self._info(self.credit_menu, "暂无可用重置卡")
            for index, credit in enumerate(visible, 1):
                self._info(
                    self.credit_menu,
                    f"{index}. {credit.get('title') or 'Full reset'} · "
                    f"{_format_credit_expiry(str(credit['expires_at']))}",
                )

        @objc.python_method
        def _build_accounts_menu(self, profiles: list[Any]) -> None:
            self.accounts_menu.removeAllItems()
            if not profiles:
                self._info(self.accounts_menu, "暂无账号")
                return
            for profile in profiles:
                name = str(_profile_value(profile, "name") or "未命名账号")
                display_name = _profile_display_name(profile)
                result = self.results.get(name)
                root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    ("✓ " if name == self.active_name else "")
                    + display_name + _account_title_suffix(result), None, ""
                )
                submenu = AppKit.NSMenu.alloc().init()
                email = _profile_value(profile, "email")
                if email:
                    self._info(submenu, f"邮箱：{email}")
                self._account_details(submenu, result)
                submenu.addItem_(AppKit.NSMenuItem.separatorItem())
                if name == self.selected_name:
                    self._info(submenu, "顶栏主账号")
                elif self.account_store is not None:
                    self._action(
                        submenu, "设为顶栏主账号", "setMainAccount:", name
                    )
                if name == self.active_name:
                    self._info(submenu, "ChatGPT 当前账号")
                elif self.account_store is not None:
                    self._action(
                        submenu, "切换到此账号并重启 ChatGPT", "switchAccount:", name
                    )
                if self.account_store is not None:
                    self._action(submenu, "重命名账号…", "renameAccount:", name)
                root.setSubmenu_(submenu)
                self.accounts_menu.addItem_(root)

        def setMainAccount_(self, sender: Any) -> None:
            name = str(sender.representedObject())
            try:
                self.account_store.set_current(name)
            except Exception as exc:
                self._alert("设置失败", str(exc))
                return
            self.refreshCurrent_(None)

        def renameAccount_(self, sender: Any) -> None:
            name = str(sender.representedObject())
            try:
                profile = self.account_store.get_account(name)
            except Exception as exc:
                self._alert("无法读取账号", str(exc))
                return
            display_name = self._prompt_display_name(_profile_display_name(profile))
            if display_name is None:
                return
            try:
                self.account_store.set_display_name(name, display_name)
            except Exception as exc:
                self._alert("重命名失败", str(exc))
                return
            self.refreshAll_(None)

        @objc.python_method
        def _account_details(self, menu: Any, result: dict[str, Any] | None) -> None:
            if result is None:
                self._info(menu, "额度：等待刷新")
                return
            usage = result["usage"]
            if usage is None:
                self._info(menu, "状态：登录态已失效或查询失败")
                self._info(menu, _short_error(result["error"] or "未知错误"))
                return
            rate = usage.get("rate_limit") or {}
            self._info(menu, f"计划：{usage.get('plan_type') or usage.get('plan') or '未知'}")
            self._info(menu, _format_limit_item("5 小时", rate.get("primary_window") or {}))
            self._info(menu, _format_limit_item("周额度", rate.get("secondary_window") or {}))
            balance = (usage.get("credits") or {}).get("balance")
            if _has_positive_balance(balance):
                self._info(menu, f"点数：{_format_balance(balance)}（{_format_balance_usd(balance)}）")
            reset = result["reset"]
            credits = reset.get("credits") or [] if reset else []
            count = (usage.get("rate_limit_reset_credits") or {}).get("available_count")
            self._info(menu, f"重置卡：{count if count is not None else len(credits)} 张")
            self._info(menu, f"更新：{result['updated'].strftime('%H:%M:%S')}")

        def switchAccount_(self, sender: Any) -> None:
            if self.switch_in_progress:
                return
            name = str(sender.representedObject())
            try:
                display_name = _profile_display_name(self.account_store.get_account(name))
            except Exception:
                display_name = name
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(f"切换到“{display_name}”并重启 ChatGPT？")
            alert.setInformativeText_(
                "将先请求 ChatGPT 正常退出，最多等待 10 秒；不会强制结束进程。"
            )
            alert.addButtonWithTitle_("切换并重启")
            alert.addButtonWithTitle_("取消")
            if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                return
            self.switch_in_progress = True
            self.switch_name = name
            running = list(AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                "com.openai.codex"
            ))
            if not running:
                self._activate_in_background(name)
                return
            for application in running:
                application.terminate()
            self.switch_deadline = monotonic() + 10
            self.switch_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.25, self, "checkChatGPTExit:", None, True
            )

        def addCurrentAccount_(self, sender: Any) -> None:
            if self.account_store is None:
                self._alert("无法添加账号", "当前未启用多账号存储。")
                return
            source = Path(self.account_store.canonical_auth_path)
            if not source.is_file():
                self._alert(
                    "未找到当前授权",
                    "请先在 ChatGPT/Codex 中完成登录，再添加当前账号。",
                )
                return
            try:
                active_name = self.account_store.detect_active_name()
            except Exception:
                self._alert("无法识别当前账号", "请检查账号存储目录的访问权限。")
                return
            if active_name:
                try:
                    active_display_name = _profile_display_name(
                        self.account_store.get_account(active_name)
                    )
                except Exception:
                    active_display_name = active_name
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_(f"当前账号已保存为“{active_display_name}”")
                alert.setInformativeText_("是否使用最新的 ChatGPT 授权更新该账号？")
                alert.addButtonWithTitle_("更新凭据")
                alert.addButtonWithTitle_("取消")
                if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                    return
                try:
                    self.account_store.snapshot_canonical()
                except Exception:
                    self._alert("更新凭据失败", "请检查认证文件和存储目录权限。")
                    return
                self._alert("凭据已更新", f"已保存“{active_display_name}”的最新 ChatGPT 授权。")
                self.refreshAll_(None)
                return
            self._import_account_source(source)

        def webAuthorizeAccount_(self, sender: Any) -> None:
            if self.account_store is None:
                self._alert("无法网页授权", "当前未启用多账号存储。")
                return
            if self.web_auth_in_progress:
                self._alert("正在等待授权", "请先在浏览器中完成上一次 ChatGPT 登录。")
                return
            display_name = self._prompt_display_name("新账号")
            if display_name is None:
                return
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("在浏览器中授权 ChatGPT")
            alert.setInformativeText_(
                "即将打开 Codex 官方登录页。请确认浏览器中选择的是你想添加的账号；"
                "授权期间不会替换当前 ChatGPT 账号。"
            )
            alert.addButtonWithTitle_("打开网页授权")
            alert.addButtonWithTitle_("取消")
            if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                return
            self.web_auth_in_progress = True
            Thread(
                target=self._run_web_authorization,
                args=(display_name,),
                name="codex-web-authorization",
                daemon=True,
            ).start()

        @objc.python_method
        def _run_web_authorization(self, display_name: str) -> None:
            name = None
            error = None
            temporary_path = None
            try:
                credential = login_with_chatgpt(timeout=600)
                existing = {
                    str(_profile_value(profile, "name"))
                    for profile in self.account_store.list_accounts()
                }
                name = _suggest_account_name(Path(f"{display_name}.json"), existing)
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".web-auth-",
                    suffix=".json",
                    dir=self.account_store.root,
                    delete=False,
                ) as handle:
                    handle.write(credential)
                    handle.flush()
                    temporary_path = Path(handle.name)
                had_current = self.account_store.get_current() is not None
                self.account_store.import_account(
                    name,
                    temporary_path,
                    display_name=display_name,
                    make_current=not had_current,
                )
            except Exception as exc:
                error = str(exc)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "finishWebAuthorization:", (display_name, name, error), False
            )

        def finishWebAuthorization_(self, payload: tuple[str, str | None, str | None]) -> None:
            display_name, name, error = payload
            self.web_auth_in_progress = False
            if error:
                self._alert("网页授权失败", error)
                return
            self._alert(
                "账号已添加",
                f"已通过 ChatGPT 网页授权并安全保存“{display_name}”。",
            )
            self.refreshAll_(None)

        def importAuthFile_(self, sender: Any) -> None:
            if self.account_store is None:
                self._alert("无法导入账号", "当前未启用多账号存储。")
                return
            panel = AppKit.NSOpenPanel.openPanel()
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(False)
            panel.setAllowsMultipleSelection_(False)
            panel.setAllowedFileTypes_(["json"])
            panel.setTitle_("选择 auth.json")
            panel.setPrompt_("导入")
            if panel.runModal() != AppKit.NSModalResponseOK:
                return
            url = panel.URL()
            if url is None:
                return
            self._import_account_source(Path(str(url.path())))

        def snapshotCurrent_(self, sender: Any) -> None:
            if self.account_store is None:
                self._alert("无法更新凭据", "当前未启用多账号存储。")
                return
            canonical = Path(self.account_store.canonical_auth_path)
            if not canonical.is_file():
                self._alert(
                    "未找到当前授权",
                    "请先在 ChatGPT/Codex 中完成登录。",
                )
                return
            try:
                profile = self.account_store.snapshot_canonical()
            except Exception:
                self._alert(
                    "更新凭据失败",
                    "无法读取或保存当前授权文件，请检查文件和目录权限。",
                )
                return
            if profile is None:
                self._alert(
                    "已保存未识别账号的备份",
                    "当前 ChatGPT 授权与已导入账号均不匹配，"
                    "已将其安全保存到账号存储目录的 backups 文件夹。",
                )
            else:
                self._alert(
                    "凭据已更新",
                    f"已保存“{_profile_display_name(profile)}”的最新 ChatGPT 授权。",
                )
            self.refreshAll_(None)

        def openAccountStore_(self, sender: Any) -> None:
            if self.account_store is None:
                self._alert("无法打开目录", "当前未启用多账号存储。")
                return
            root = Path(self.account_store.root)
            if not AppKit.NSWorkspace.sharedWorkspace().openFile_(str(root)):
                self._alert("无法打开目录", "请检查账号存储目录是否存在。")

        @objc.python_method
        def _import_account_source(self, source: Path) -> None:
            try:
                profiles = list(self.account_store.list_accounts())
                existing = {
                    str(_profile_value(profile, "name")) for profile in profiles
                }
            except Exception:
                self._alert(
                    "无法读取账号列表",
                    "请检查账号存储目录的访问权限。",
                )
                return
            display_name = self._prompt_display_name(
                _suggest_display_name(source)
            )
            if display_name is None:
                return
            name = _suggest_account_name(source, existing)
            try:
                had_current = self.account_store.get_current() is not None
                self.account_store.import_account(
                    name,
                    source,
                    display_name=display_name,
                    make_current=False,
                )
                if not had_current:
                    self.account_store.set_current(name)
            except Exception:
                self._alert(
                    "账号导入失败",
                    "请确认所选文件是有效的 auth.json，"
                    "并检查账号存储目录的访问权限。",
                )
                return
            self._alert(
                "账号已导入",
                f"已安全保存账号“{display_name}”。",
            )
            self.refreshAll_(None)

        @objc.python_method
        def _prompt_display_name(self, suggestion: str) -> str | None:
            value = suggestion
            while True:
                field = AppKit.NSTextField.alloc().initWithFrame_(
                    AppKit.NSMakeRect(0, 0, 320, 24)
                )
                field.setStringValue_(value)
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("设置账号名称")
                alert.setInformativeText_(
                    "可以使用中文、英文、数字和空格，长度为 1~64 个字符。"
                )
                alert.setAccessoryView_(field)
                alert.addButtonWithTitle_("继续")
                alert.addButtonWithTitle_("取消")
                alert.window().setInitialFirstResponder_(field)
                if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                    return None
                value = str(field.stringValue()).strip()
                if _is_valid_display_name(value):
                    return value
                self._alert(
                    "账号名称无效",
                    "请输入 1~64 个字符，不要包含换行或其他控制字符。",
                )

        @objc.python_method
        def _confirm_replace(self, name: str) -> bool:
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(f"替换已有账号“{name}”？")
            alert.setInformativeText_(
                "将覆盖该别名下已保存的授权文件，此操作不会切换 ChatGPT 账号。"
            )
            alert.addButtonWithTitle_("替换")
            alert.addButtonWithTitle_("取消")
            return alert.runModal() == AppKit.NSAlertFirstButtonReturn

        def checkChatGPTExit_(self, timer: Any) -> None:
            running = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                "com.openai.codex"
            )
            if not running:
                timer.invalidate()
                self._activate_in_background(self.switch_name)
            elif monotonic() >= self.switch_deadline:
                timer.invalidate()
                self.switch_in_progress = False
                self._alert("切换已取消", "ChatGPT 在 10 秒内未退出，账号文件未更改。")

        @objc.python_method
        def _activate_in_background(self, name: str) -> None:
            def activate() -> None:
                error = None
                try:
                    self.account_store.activate(name)
                except Exception as exc:
                    error = str(exc)
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "finishSwitch:", (name, error), False
                )
            Thread(target=activate, name="codex-account-activate", daemon=True).start()

        def finishSwitch_(self, payload: tuple[str, str | None]) -> None:
            name, error = payload
            self.switch_in_progress = False
            if error:
                self._alert("账号切换失败", error)
                return
            workspace = AppKit.NSWorkspace.sharedWorkspace()
            app_url = workspace.URLForApplicationWithBundleIdentifier_("com.openai.codex")
            if app_url is None or not workspace.openURL_(app_url):
                self._alert("账号已切换", "无法自动打开 ChatGPT，请手动启动。")
            self.refreshAll_(None)

        @objc.python_method
        def _alert(self, title: str, detail: str) -> None:
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(title)
            alert.setInformativeText_(detail)
            alert.addButtonWithTitle_("好")
            alert.runModal()

        def noop_(self, sender: Any) -> None:
            pass

        def quit_(self, sender: Any) -> None:
            AppKit.NSApplication.sharedApplication().terminate_(self)

    controller = MultiAccountController.alloc().init()
    controller.auth_file = Path(auth_file)
    controller.base_url = base_url
    controller.account_store = account_store
    controller.interval = max(60, int(interval))
    controller.all_interval = max(300, controller.interval)
    controller.refreshing = False
    controller.pending_refresh_all = False
    controller.last_all_refresh = 0.0
    controller.results = {}
    controller.selected_name = None
    controller.active_name = None
    controller.switch_in_progress = False
    controller.web_auth_in_progress = False
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


def _profile_value(profile: Any, key: str) -> Any:
    if isinstance(profile, dict):
        return profile.get(key)
    return getattr(profile, key, None)


def _profile_display_name(profile: Any) -> str:
    if profile is None:
        return "未命名账号"
    return str(
        _profile_value(profile, "display_name")
        or _profile_value(profile, "name")
        or "未命名账号"
    )


def _is_valid_display_name(name: str) -> bool:
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 64
        and not any(ord(character) < 32 or ord(character) == 127 for character in name)
    )


def _is_valid_account_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name))


def _suggest_display_name(source: Path) -> str:
    raw = source.parent.name if source.name.lower() == "auth.json" else source.stem
    value = raw.strip()
    return value[:64] if _is_valid_display_name(value[:64]) else "新账号"


def _suggest_account_name(source: Path, existing: set[str]) -> str:
    """Build a non-secret, store-safe alias from a selected file location."""

    raw = source.parent.name if source.name.lower() == "auth.json" else source.stem
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", raw.encode("ascii", "ignore").decode())
    base = base.strip("_-")[:64]
    if not base or not base[0].isalnum():
        base = "account"
    if base not in existing:
        return base
    index = 2
    while True:
        suffix = f"-{index}"
        candidate = f"{base[: 64 - len(suffix)]}{suffix}"
        if candidate not in existing:
            return candidate
        index += 1


def _account_title_suffix(result: dict[str, Any] | None) -> str:
    if result is None:
        return " · 等待刷新"
    usage = result.get("usage")
    if usage is None:
        return " · 查询失败"
    primary = (usage.get("rate_limit") or {}).get("primary_window") or {}
    used_percent = primary.get("used_percent")
    if used_percent is None:
        return " · 5h 暂无"
    remaining = max(0, 100 - _to_percent(used_percent))
    return f" · 5h 剩余 {remaining}%"


def _short_error(error: str, limit: int = 90) -> str:
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
    if not window or window.get("used_percent") is None:
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
