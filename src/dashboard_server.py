from __future__ import annotations

import json
import mimetypes
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.codex_usage import CodexUsageError, get_codex_reset_credits, get_codex_usage


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
) -> str:
    actual_port = find_available_port(host, port)
    web_dir = Path(__file__).resolve().parent.parent / "web"

    class Handler(DashboardHandler):
        pass

    Handler.web_dir = web_dir
    Handler.auth_file = auth_file
    Handler.base_url = base_url

    server = ThreadingHTTPServer((host, actual_port), Handler)
    url = f"http://{host}:{actual_port}"
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


class DashboardHandler(BaseHTTPRequestHandler):
    web_dir: Path
    auth_file: Path
    base_url: str

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/codex":
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
        try:
            payload = {
                "usage": get_codex_usage(auth_path=self.auth_file, base_url=self.base_url),
                "reset_credits": get_codex_reset_credits(auth_path=self.auth_file, base_url=self.base_url),
            }
            self._send_json(200, payload)
        except CodexUsageError as exc:
            self._send_json(500, {"error": str(exc)})

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
