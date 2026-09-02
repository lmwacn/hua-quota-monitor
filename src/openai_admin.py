from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class OpenAIAdminError(RuntimeError):
    pass


@dataclass
class AdminClient:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout: int = 60

    def get_usage(
        self,
        *,
        usage_type: str,
        start_time: int,
        end_time: int,
        bucket_width: str,
        group_by: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        allowed = {
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
        }
        if usage_type not in allowed:
            raise OpenAIAdminError(f"不支持的 Usage 类型：{usage_type}")
        return self._get_paginated(
            f"/organization/usage/{usage_type}",
            {
                "start_time": start_time,
                "end_time": end_time,
                "bucket_width": bucket_width,
                "limit": limit,
                "group_by": group_by,
            },
        )

    def get_costs(
        self,
        *,
        start_time: int,
        end_time: int,
        bucket_width: str,
        group_by: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "bucket_width": bucket_width,
            "limit": limit,
        }
        if group_by:
            params["group_by"] = group_by
        return self._get_paginated("/organization/costs", params)

    def _get_paginated(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        all_data: list[dict[str, Any]] = []
        page: str | None = None

        while True:
            query = dict(params)
            if page:
                query["page"] = page
            payload = self._request_json("GET", path, query)
            data = payload.get("data", [])
            if not isinstance(data, list):
                raise OpenAIAdminError(f"响应 data 字段不是列表：{payload}")
            all_data.extend(data)

            page = payload.get("next_page")
            if not page:
                break

        return all_data

    def _request_json(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        query = _encode_params(params)
        if query:
            url = f"{url}?{query}"

        request = urllib.request.Request(
            url=url,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "hua-quota-monitor/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAIAdminError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIAdminError(str(exc)) from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OpenAIAdminError(f"无法解析 JSON 响应：{body[:500]}") from exc
        if not isinstance(parsed, dict):
            raise OpenAIAdminError(f"响应不是 JSON 对象：{parsed}")
        return parsed


def _encode_params(params: dict[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None or value == []:
            continue
        if isinstance(value, list):
            for item in value:
                pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))
    return urllib.parse.urlencode(pairs)
