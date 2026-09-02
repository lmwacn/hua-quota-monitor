from __future__ import annotations

import json
import mimetypes
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.account_store import AccountStore, AccountStoreError
from src.quota_service import QuotaService
from src.rate_windows import usage_rate_limit_sections
from src.usage_monitor import UsageMonitor


DEFAULT_DASHBOARD_PORT = 48763


def find_available_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"没有找到可用端口：{preferred}-{preferred + 99}")


def run_dashboard(
    *,
    host: str,
    port: int,
    auth_file: Path,
    base_url: str,
    open_browser: bool,
    store_dir: Path = Path("~/.hua-quota").expanduser(),
) -> str:
    server, url = create_dashboard_server(
        host=host,
        port=port,
        auth_file=auth_file,
        base_url=base_url,
        store_dir=store_dir,
    )
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        print(f"Codex 可视化面板已启动：{url}")
        print("按 Ctrl+C 停止服务。")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止面板服务。")
    finally:
        server.server_close()
    return url


def create_dashboard_server(
    *,
    host: str,
    port: int,
    auth_file: Path,
    base_url: str,
    store_dir: Path,
) -> tuple[ThreadingHTTPServer, str]:
    """Create a dashboard server without starting its blocking serve loop."""
    actual_port = find_available_port(host, port)
    web_dir = Path(__file__).resolve().parent.parent / "web"

    class Handler(DashboardHandler):
        pass

    Handler.web_dir = web_dir
    account_store = AccountStore(
        root=store_dir,
        canonical_auth_path=auth_file,
    )
    monitor = UsageMonitor(store_dir / "usage-history.sqlite3")
    Handler.quota_service = QuotaService(
        auth_file=auth_file,
        base_url=base_url,
        monitor=monitor,
        account_store=account_store,
    )

    server = ThreadingHTTPServer((host, actual_port), Handler)
    url = f"http://{host}:{actual_port}"
    return server, url


class DashboardHandler(BaseHTTPRequestHandler):
    web_dir: Path
    quota_service: QuotaService

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/accounts":
            self._handle_accounts()
            return
        if path == "/api/codex":
            account_name = query.get("account", [None])[0]
            if query.get("cached") == ["1"]:
                self._handle_cached_codex(account_name)
            else:
                self._handle_codex(account_name)
            return
        if path == "/":
            self._serve_file(self.web_dir / "dashboard.html")
            return
        self._serve_file(self.web_dir / path.lstrip("/"))

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/codex":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        target = self.web_dir / "dashboard.html" if path == "/" else self.web_dir / path.lstrip("/")
        if not target.exists() or not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_accounts(self) -> None:
        try:
            profiles, selected, _ = self.quota_service.profiles()
        except AccountStoreError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(
            200,
            {
                "accounts": [
                    {
                        "name": profile.name,
                        "display_name": profile.display_name,
                        "email": profile.email,
                        "is_current": profile.is_current,
                        "is_active": profile.is_active,
                        "available": profile.error is None,
                    }
                    for profile in profiles
                ],
                "default_account": selected,
            },
        )

    def _handle_codex(self, account_name: str | None) -> None:
        profile = self._resolve_profile(account_name)
        if profile is None:
            return
        result = self.quota_service.fetch_profile(profile)
        if result["usage"] is None:
            self._send_json(500, {"error": result["error"] or "额度读取失败"})
            return
        self._send_json(
            200,
            self._dashboard_payload(
                result=result,
                meta={
                    "cached": result["usage_cached"],
                    "observed_at": result["usage_observed_at"],
                    "usage_error": result["usage_error"],
                    "reset_error": result["reset_error"],
                    "account_name": account_name,
                },
            ),
        )

    def _handle_cached_codex(self, account_name: str | None) -> None:
        profile = self._resolve_profile(account_name)
        if profile is None:
            return
        result = self.quota_service.cached_profile(profile)
        if result is None:
            self._send_json(404, {"error": "暂无 10 分钟内的本地快照"})
            return
        self._send_json(
            200,
            self._dashboard_payload(
                result=result,
                meta={
                    "cached": True,
                    "initial_cache": True,
                    "observed_at": result["usage_observed_at"],
                    "usage_error": None,
                    "reset_error": None,
                    "account_name": account_name,
                },
            ),
        )

    def _resolve_profile(self, account_name: str | None) -> Any | None:
        try:
            return self.quota_service.resolve_profile(account_name)
        except (AccountStoreError, ValueError) as exc:
            self._send_json(404, {"error": str(exc)})
            return None

    def _dashboard_payload(
        self,
        *,
        result: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "usage": result["usage"],
            "quota_sections": usage_rate_limit_sections(result["usage"]),
            "reset_credits": result["reset"] or {},
            "history": self.quota_service.current_history(result, hours=24),
            "meta": meta,
        }

    def _serve_file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            web_root = self.web_dir.resolve()
            if web_root not in resolved.parents and resolved != web_root:
                self._send_json(403, {"error": "forbidden"})
                return
            if not resolved.exists() or not resolved.is_file():
                self._send_json(404, {"error": "not found"})
                return
            content = resolved.read_bytes()
        except OSError as exc:
            self._send_json(500, {"error": str(exc)})
            return

        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self._write_content(content)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self._write_content(content)

    def _write_content(self, content: bytes) -> None:
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            # Switching accounts can cancel an older in-flight browser request.
            return
