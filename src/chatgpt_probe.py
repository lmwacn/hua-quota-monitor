from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ChatGPTProbeConfig:
    url: str
    cookie_file: Path | None = None
    bearer_token: str | None = None
    method: str = "GET"
    timeout: int = 60


def probe_chatgpt_session(config: ChatGPTProbeConfig) -> dict[str, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "hua-quota-monitor/0.1",
    }
    if config.cookie_file:
        cookie = read_cookie_file(config.cookie_file)
        if cookie:
            headers["Cookie"] = cookie
    if config.bearer_token:
        headers["Authorization"] = f"Bearer {config.bearer_token}"

    request = urllib.request.Request(config.url, method=config.method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            status = response.status
            content_type = response.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "error": text[:2000],
            "hint": "接口拒绝请求。请检查 cookie/token 是否有效，或该网页端接口是否需要额外 headers。",
        }

    parsed: Any
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"raw_text_preview": text[:2000]}

    return {
        "ok": 200 <= status < 300,
        "status": status,
        "content_type": content_type,
        "data": parsed,
        "quota_candidates": find_quota_like_fields(parsed),
    }


def read_cookie_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""

    if "\t" not in text and "=" in text and "\n" not in text:
        return text

    pairs: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 7:
                pairs.append(f"{parts[-2]}={parts[-1]}")
        elif "=" in line:
            pairs.append(line)
    return "; ".join(pairs)


def find_quota_like_fields(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    keys = ("quota", "limit", "usage", "used", "remaining", "reset", "cap", "allowance", "rate")
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if any(marker in str(key).lower() for marker in keys):
                hits.append({"path": path, "value": child})
            hits.extend(find_quota_like_fields(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            path = f"{prefix}[{index}]"
            hits.extend(find_quota_like_fields(child, path))
    return hits[:100]
