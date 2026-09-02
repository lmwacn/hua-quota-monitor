from __future__ import annotations

import json
import mimetypes
import socket
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.codex_usage import (
    CodexUsageError,
    get_codex_reset_credits,
    get_codex_usage,
    load_codex_auth,
)
from src.usage_monitor import UsageMonitor, usage_window_keys


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
    Handler.auth_file = auth_file
    Handler.base_url = base_url
    Handler.usage_monitor = UsageMonitor(store_dir / "usage-history.sqlite3")

    server = ThreadingHTTPServer((host, actual_port), Handler)
    url = f"http://{host}:{actual_port}"
    return server, url


class DashboardHandler(BaseHTTPRequestHandler):
    web_dir: Path
    auth_file: Path
    base_url: str
    usage_monitor: UsageMonitor

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/codex":
            if parse_qs(parsed.query).get("cached") == ["1"]:
                self._handle_cached_codex()
            else:
                self._handle_codex()
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

    def _handle_codex(self) -> None:
        account_key = self._account_key()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dashboard-api") as pool:
            usage_future = pool.submit(
                get_codex_usage,
                auth_path=self.auth_file,
                base_url=self.base_url,
            )
            reset_future = pool.submit(
                get_codex_reset_credits,
                auth_path=self.auth_file,
                base_url=self.base_url,
            )

            usage_error = None
            try:
                usage = usage_future.result()
            except CodexUsageError as exc:
                usage_error = str(exc)
                cached = self.usage_monitor.cached_usage(account_key)
                if cached is None:
                    self._send_json(500, {"error": usage_error})
                    return
                usage, observed_at = cached
                cached_response = True
            else:
                observed_at = self.usage_monitor.record_success(account_key, usage)
                cached_response = False

            reset_error = None
            try:
                reset_credits = reset_future.result()
            except CodexUsageError as exc:
                reset_credits = {}
                reset_error = str(exc)

        self._send_json(
            200,
            self._dashboard_payload(
                account_key=account_key,
                usage=usage,
                reset_credits=reset_credits,
                meta={
                    "cached": cached_response,
                    "observed_at": observed_at,
                    "usage_error": usage_error,
                    "reset_error": reset_error,
                },
            ),
        )

    def _handle_cached_codex(self) -> None:
        account_key = self._account_key()
        cached = self.usage_monitor.cached_usage(account_key)
        if cached is None:
            self._send_json(404, {"error": "暂无 10 分钟内的本地快照"})
            return
        usage, observed_at = cached
        self._send_json(
            200,
            self._dashboard_payload(
                account_key=account_key,
                usage=usage,
                reset_credits={},
                meta={
                    "cached": True,
                    "initial_cache": True,
                    "observed_at": observed_at,
                    "usage_error": None,
                    "reset_error": None,
                },
            ),
        )

    def _account_key(self) -> str:
        try:
            auth = load_codex_auth(self.auth_file)
            return auth.account_id or str(self.auth_file.resolve())
        except CodexUsageError:
            return str(self.auth_file.resolve())

    def _dashboard_payload(
        self,
        *,
        account_key: str,
        usage: dict[str, Any],
        reset_credits: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        active_window_keys = usage_window_keys(usage)
        history = [
            row
            for row in self.usage_monitor.history(account_key, hours=24)
            if row["window_key"] in active_window_keys
        ]
        return {
            "usage": usage,
            "reset_credits": reset_credits,
            "history": history,
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
        self.wfile.write(content)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
