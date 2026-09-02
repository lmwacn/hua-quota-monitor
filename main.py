#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.account_store import AccountStore, AccountStoreError
from src.chatgpt_probe import ChatGPTProbeConfig, probe_chatgpt_session
from src.codex_usage import CodexUsageError, get_codex_reset_credits, get_codex_usage
from src.dashboard_server import DEFAULT_DASHBOARD_PORT, run_dashboard
from src.codex_widget import create_menubar_shortcut, run_menubar
from src.openai_admin import AdminClient, OpenAIAdminError
from src.render import print_codex_reset_credits, print_codex_usage, print_cost_summary, print_usage_summary
from src.time_windows import resolve_window


CONFIG_PATH = Path("config.example.json")
DEFAULT_CODEX_AUTH_FILE = "~/.codex/auth.json"


def _account_store(args: argparse.Namespace) -> AccountStore:
    return AccountStore(
        root=Path(args.store_dir).expanduser(),
        canonical_auth_path=Path(args.codex_auth_file).expanduser(),
    )


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_group_by(values: list[str] | None) -> list[str]:
    if not values:
        return []
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def cmd_api(args: argparse.Namespace) -> int:
    load_dotenv()
    api_key = args.admin_key or os.environ.get("OPENAI_ADMIN_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("缺少 OPENAI_ADMIN_KEY 或 OPENAI_API_KEY。请在 .env 中配置，或通过 --admin-key 传入。", file=sys.stderr)
        return 2

    start, end, bucket_width = resolve_window(args.window, args.bucket)
    client = AdminClient(api_key=api_key, base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    group_by = parse_group_by(args.group_by)

    try:
        if args.kind in ("usage", "all"):
            usage = client.get_usage(
                usage_type=args.usage_type,
                start_time=start,
                end_time=end,
                bucket_width=bucket_width,
                group_by=group_by,
                limit=args.limit,
            )
            if args.json:
                print(json.dumps({"usage": usage}, ensure_ascii=False, indent=2))
            else:
                print_usage_summary(usage, args.usage_type, start, end, bucket_width, group_by)

        if args.kind in ("costs", "all"):
            costs = client.get_costs(
                start_time=start,
                end_time=end,
                bucket_width=bucket_width,
                group_by=group_by,
                limit=args.limit,
            )
            if args.json:
                print(json.dumps({"costs": costs}, ensure_ascii=False, indent=2))
            else:
                print_cost_summary(costs, start, end, bucket_width, group_by)
    except OpenAIAdminError as exc:
        print(f"OpenAI Admin API 查询失败：{exc}", file=sys.stderr)
        return 1

    return 0


def cmd_chatgpt(args: argparse.Namespace) -> int:
    config = ChatGPTProbeConfig(
        url=args.url,
        cookie_file=Path(args.cookie_file) if args.cookie_file else None,
        bearer_token=args.bearer_token,
        method=args.method,
    )
    try:
        result = probe_chatgpt_session(config)
    except Exception as exc:
        print(f"ChatGPT 登录态探测失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_codex(args: argparse.Namespace) -> int:
    auth_path = Path(args.auth_file).expanduser()
    try:
        usage_payload = get_codex_usage(auth_path=auth_path, base_url=args.base_url)
        credits_payload = None
        if args.include_reset_credits:
            credits_payload = get_codex_reset_credits(auth_path=auth_path, base_url=args.base_url)
    except CodexUsageError as exc:
        print(f"Codex 额度查询失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {"usage": usage_payload}
        if credits_payload is not None:
            payload["reset_credits"] = credits_payload
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_codex_usage(usage_payload)
        if credits_payload is not None:
            print_codex_reset_credits(credits_payload)
    return 0


def cmd_windows(_: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    options = ["5h", "today", "week", "month", "30d"]
    rows = []
    for name in options:
        start, end, bucket = resolve_window(name, None)
        rows.append(
            {
                "window": name,
                "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                "end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat(),
                "bucket_width": bucket,
            }
        )
    print(json.dumps({"now_utc": now.isoformat(), "windows": rows}, ensure_ascii=False, indent=2))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    run_dashboard(
        host=args.host,
        port=args.port,
        auth_file=Path(args.auth_file).expanduser(),
        base_url=args.base_url,
        open_browser=not args.no_open,
        store_dir=Path(args.account_store).expanduser(),
    )
    return 0


def cmd_menubar(args: argparse.Namespace) -> int:
    if args.install_shortcut:
        shortcut = create_menubar_shortcut(
            project_root=Path(__file__).resolve().parent,
            interval=args.interval,
            name=args.shortcut_name,
        )
        print(f"桌面快捷按钮已创建：{shortcut}")
        return 0
    store = AccountStore(
        root=Path(args.account_store).expanduser(),
        canonical_auth_path=Path(args.auth_file).expanduser(),
    )
    if not store.list_accounts() and Path(args.auth_file).expanduser().is_file():
        try:
            store.import_account(
                "current",
                args.auth_file,
                display_name="当前账号",
                make_current=True,
            )
            print("已将当前 Codex 账号纳入监控，名称：当前账号")
        except AccountStoreError as exc:
            print(f"无法初始化账号监控：{exc}", file=sys.stderr)
            return 1
    return run_menubar(
        auth_file=Path(args.auth_file).expanduser(),
        account_store=store,
        base_url=args.base_url,
        interval=args.interval,
        width=args.width,
        height=args.height,
        alpha=args.alpha,
    )


def cmd_account(args: argparse.Namespace) -> int:
    try:
        store = _account_store(args)
        if args.account_command == "import":
            profile = store.import_account(
                args.name,
                Path(args.auth_file).expanduser(),
                display_name=args.display_name,
                make_current=args.current or not store.list_accounts(),
                replace=args.replace,
            )
            print(f"已导入账号：{profile.display_name}（{profile.email or profile.account_id or '未知账号'}）")
            return 0

        if args.account_command == "list":
            profiles = store.list_accounts()
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                "name": profile.name,
                                "display_name": profile.display_name,
                                "email": profile.email,
                                "account_id": profile.account_id,
                                "monitoring": profile.is_current,
                                "active": profile.is_active,
                                "error": profile.error,
                            }
                            for profile in profiles
                        ],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            if not profiles:
                print("还没有导入账号。")
                return 0
            for profile in profiles:
                flags = []
                if profile.is_active:
                    flags.append("Codex 当前")
                if profile.is_current:
                    flags.append("顶栏主账号")
                suffix = f" [{', '.join(flags)}]" if flags else ""
                identity = profile.email or profile.account_id or "未知账号"
                error = f" · {profile.error}" if profile.error else ""
                internal = f" ({profile.name})" if profile.display_name != profile.name else ""
                print(f"- {profile.display_name}{internal}: {identity}{suffix}{error}")
            return 0

        if args.account_command == "current":
            profile = store.get_current()
            if profile is None:
                print("未设置顶栏主账号。")
                return 1
            print(f"{profile.display_name}\t{profile.email or profile.account_id or '-'}")
            return 0

        if args.account_command == "use":
            profile = store.set_current(args.name)
            print(f"顶栏主账号已设为：{profile.display_name}")
            return 0

        if args.account_command == "rename":
            profile = store.set_display_name(args.name, args.display_name)
            print(f"账号已重命名为：{profile.display_name}")
            return 0

    except AccountStoreError as exc:
        print(f"账号操作失败：{exc}", file=sys.stderr)
        return 1
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hua-quota-monitor",
        description="本地监控 Codex 额度，并查询 OpenAI API 用量与费用。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    api = subparsers.add_parser("api", help="查询官方 OpenAI Admin Usage/Costs API")
    api.add_argument("--kind", choices=["usage", "costs", "all"], default="all")
    api.add_argument(
        "--usage-type",
        choices=[
            "completions",
            "embeddings",
            "moderations",
            "images",
            "audio_speeches",
            "audio_transcriptions",
            "vector_stores",
            "code_interpreter_sessions",
            "file_searches",
            "web_searches",
        ],
        default="completions",
        help="Usage API 资源类型，默认 completions",
    )
    api.add_argument("--window", default="5h", help="时间窗口：5h、today、week、month、30d，或 2026-07-01..2026-07-02")
    api.add_argument("--bucket", choices=["1m", "1h", "1d"], default=None)
    api.add_argument("--group-by", action="append", help="分组字段，如 model、project_id、api_key_id、user_id")
    api.add_argument("--limit", type=int, default=100)
    api.add_argument("--admin-key", default=None)
    api.add_argument("--json", action="store_true", help="输出原始 JSON")
    api.set_defaults(func=cmd_api)

    chatgpt = subparsers.add_parser("chatgpt", help="用本地登录态请求一个 ChatGPT 网页端接口")
    chatgpt.add_argument("--url", required=True, help="要探测的 ChatGPT 网页端接口 URL")
    chatgpt.add_argument("--cookie-file", help="Netscape/curl 格式或纯 Cookie header 文本文件")
    chatgpt.add_argument("--bearer-token", help="可选 Bearer token")
    chatgpt.add_argument("--method", choices=["GET", "POST"], default="GET")
    chatgpt.set_defaults(func=cmd_chatgpt)

    codex = subparsers.add_parser("codex", help="查询 Codex/ChatGPT 登录态对应的 Codex 额度")
    codex.add_argument(
        "--auth-file",
        default=DEFAULT_CODEX_AUTH_FILE,
        help="ChatGPT/Codex 登录文件路径（默认读取 ~/.codex/auth.json）",
    )
    codex.add_argument("--base-url", default="https://chatgpt.com")
    codex.add_argument("--no-reset-credits", dest="include_reset_credits", action="store_false", help="不查询重置卡明细")
    codex.add_argument("--json", action="store_true", help="输出原始 JSON")
    codex.set_defaults(include_reset_credits=True)
    codex.set_defaults(func=cmd_codex)

    dashboard = subparsers.add_parser("dashboard", help="启动本地 Codex 额度可视化面板")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=DEFAULT_DASHBOARD_PORT)
    dashboard.add_argument(
        "--auth-file",
        default=DEFAULT_CODEX_AUTH_FILE,
        help="ChatGPT/Codex 登录文件路径（默认读取 ~/.codex/auth.json）",
    )
    dashboard.add_argument("--base-url", default="https://chatgpt.com")
    dashboard.add_argument(
        "--account-store",
        default="~/.hua-quota",
        help="额度历史数据目录（默认 ~/.hua-quota）",
    )
    dashboard.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    dashboard.set_defaults(func=cmd_dashboard)

    windows = subparsers.add_parser("windows", help="查看内置时间窗口会解析成什么")
    windows.set_defaults(func=cmd_windows)

    menubar = subparsers.add_parser("menubar", help="启动 macOS 顶栏额度显示")
    menubar.add_argument(
        "--auth-file",
        default=DEFAULT_CODEX_AUTH_FILE,
        help="ChatGPT/Codex 登录文件路径（默认读取 ~/.codex/auth.json）",
    )
    menubar.add_argument("--base-url", default="https://chatgpt.com")
    menubar.add_argument(
        "--account-store",
        default="~/.hua-quota",
        help="多账号凭据目录（默认 ~/.hua-quota）",
    )
    menubar.add_argument("--interval", type=int, default=60, help="自动刷新间隔（秒）")
    menubar.add_argument("--width", type=int, default=320)
    menubar.add_argument("--height", type=int, default=250)
    menubar.add_argument("--alpha", type=float, default=0.94, help="详细面板透明度（0.2~1.0）")
    menubar.add_argument("--install-shortcut", action="store_true", help="在桌面创建菜单栏启动按钮")
    menubar.add_argument("--shortcut-name", default="打开Codex顶栏", help="快捷按钮文件名（不带后缀）")
    menubar.set_defaults(func=cmd_menubar)

    account = subparsers.add_parser("account", help="管理顶栏监控的 Codex 账号")
    account.add_argument(
        "--store-dir",
        default="~/.hua-quota",
        help="多账号凭据目录（默认 ~/.hua-quota）",
    )
    account.add_argument(
        "--codex-auth-file",
        default=DEFAULT_CODEX_AUTH_FILE,
        help="Codex 当前登录文件（默认 ~/.codex/auth.json）",
    )
    account_subparsers = account.add_subparsers(dest="account_command", required=True)

    account_import = account_subparsers.add_parser("import", help="导入一个 auth.json")
    account_import.add_argument("name", help="账号名称，仅支持字母、数字、下划线和连字号")
    account_import.add_argument("--display-name", help="菜单中显示的名称，可使用中文")
    account_import.add_argument("--auth-file", default=DEFAULT_CODEX_AUTH_FILE)
    account_import.add_argument("--current", action="store_true", help="同时设为顶栏主账号")
    account_import.add_argument("--replace", action="store_true", help="覆盖同名账号")

    account_list = account_subparsers.add_parser("list", help="列出已导入账号")
    account_list.add_argument("--json", action="store_true")
    account_subparsers.add_parser("current", help="查看顶栏主账号")

    account_use = account_subparsers.add_parser("use", help="设置顶栏主账号，不改变 Codex 登录")
    account_use.add_argument("name")

    account_rename = account_subparsers.add_parser("rename", help="修改账号的菜单显示名")
    account_rename.add_argument("name", help="内部账号名")
    account_rename.add_argument("display_name", help="新的显示名，可使用中文")

    account.set_defaults(func=cmd_account)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
