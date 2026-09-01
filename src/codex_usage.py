from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CodexUsageError(RuntimeError):
    pass


@dataclass
class CodexAuth:
    access_token: str
    account_id: str | None


def load_codex_auth(path: Path) -> CodexAuth:
    if not path.exists():
        raise CodexUsageError(
            f"找不到 ChatGPT/Codex 登录文件：{path}。请先在 ChatGPT 客户端登录，"
            "或使用 --auth-file 指定有效的 auth.json。"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        tokens = payload["tokens"]
        access_token = tokens["access_token"]
        account_id = tokens.get("account_id")
    except Exception as exc:
        raise CodexUsageError(f"无法读取 ChatGPT/Codex 登录文件结构：{path}") from exc
    return CodexAuth(access_token=access_token, account_id=account_id)


def get_codex_usage(auth_path: Path, base_url: str = "https://chatgpt.com") -> dict[str, Any]:
    auth = load_codex_auth(auth_path)
    return _get_json(auth, base_url.rstrip("/") + "/backend-api/wham/usage")


def get_codex_reset_credits(auth_path: Path, base_url: str = "https://chatgpt.com") -> dict[str, Any]:
    auth = load_codex_auth(auth_path)
    return _get_json(auth, base_url.rstrip("/") + "/backend-api/wham/rate-limit-reset-credits")


def _get_json(auth: CodexAuth, url: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "Accept": "application/json",
        "User-Agent": "gpt-quota-local/0.1",
    }
    if auth.account_id:
        headers["ChatGPT-Account-Id"] = auth.account_id

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code in (401, 403):
            raise CodexUsageError(
                f"HTTP {exc.code}: ChatGPT/Codex 登录态无效或已过期。"
                "请先在 ChatGPT 客户端重新登录，或使用 --auth-file 指定最新的 auth.json。"
            ) from exc
        raise CodexUsageError(f"HTTP {exc.code}: {detail[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise CodexUsageError(str(exc)) from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodexUsageError(f"无法解析 Codex JSON：{text[:1000]}") from exc
    if not isinstance(payload, dict):
        raise CodexUsageError("Codex 响应不是 JSON 对象")
    return payload
