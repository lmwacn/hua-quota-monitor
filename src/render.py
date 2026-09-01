from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from src.rate_windows import rate_limit_windows


def print_usage_summary(
    buckets: list[dict[str, Any]],
    usage_type: str,
    start: int,
    end: int,
    bucket_width: str,
    group_by: list[str],
) -> None:
    totals = defaultdict(int)
    rows: list[dict[str, Any]] = []

    for bucket in buckets:
        for result in bucket.get("results", []):
            input_tokens = int(result.get("input_tokens") or 0)
            output_tokens = int(result.get("output_tokens") or 0)
            cached_tokens = int(result.get("input_cached_tokens") or 0)
            requests = int(result.get("num_model_requests") or 0)
            images = int(result.get("images") or 0)
            seconds = int(result.get("seconds") or 0)
            characters = int(result.get("characters") or 0)
            bytes_used = int(result.get("usage_bytes") or 0)
            sessions = int(result.get("num_sessions") or 0)
            file_requests = int(result.get("num_requests") or 0)
            totals["input_tokens"] += input_tokens
            totals["output_tokens"] += output_tokens
            totals["cached_tokens"] += cached_tokens
            totals["requests"] += requests
            totals["images"] += images
            totals["seconds"] += seconds
            totals["characters"] += characters
            totals["bytes_used"] += bytes_used
            totals["sessions"] += sessions
            totals["file_requests"] += file_requests
            rows.append(
                {
                    "start": _fmt_ts(bucket.get("start_time")),
                    "requests": requests,
                    "num_requests": file_requests,
                    "input": input_tokens,
                    "cached": cached_tokens,
                    "output": output_tokens,
                    "images": images,
                    "seconds": seconds,
                    "chars": characters,
                    "bytes": bytes_used,
                    "sessions": sessions,
                    "model": result.get("model") or "-",
                    "project": result.get("project_id") or "-",
                    "api_key": result.get("api_key_id") or "-",
                }
            )

    print(f"\nOpenAI API 用量：{usage_type}")
    print(f"范围：{_fmt_ts(start)} -> {_fmt_ts(end)}，bucket={bucket_width}，group_by={group_by or '-'}")
    print(
        f"总请求：{totals['requests']} | 输入 token：{totals['input_tokens']} | "
        f"缓存输入 token：{totals['cached_tokens']} | 输出 token：{totals['output_tokens']} | "
        f"图片：{totals['images']} | 音频秒数：{totals['seconds']} | 字符：{totals['characters']} | "
        f"存储字节：{totals['bytes_used']} | 会话：{totals['sessions']} | 调用：{totals['file_requests']}"
    )
    if rows:
        columns = _usage_columns(usage_type)
        print_table(rows[-30:], columns)
    else:
        print("没有查询到用量数据。")


def print_cost_summary(
    buckets: list[dict[str, Any]],
    start: int,
    end: int,
    bucket_width: str,
    group_by: list[str],
) -> None:
    total = 0.0
    currency = "usd"
    rows: list[dict[str, Any]] = []

    for bucket in buckets:
        bucket_total = 0.0
        for result in bucket.get("results", []):
            amount = result.get("amount") or {}
            value = float(amount.get("value") or 0)
            currency = amount.get("currency") or currency
            bucket_total += value
            total += value
            rows.append(
                {
                    "start": _fmt_ts(bucket.get("start_time")),
                    "cost": f"{value:.6f}",
                    "currency": currency,
                    "project": result.get("project_id") or "-",
                    "api_key": result.get("api_key_id") or "-",
                    "line_item": result.get("line_item") or "-",
                }
            )
        if not bucket.get("results"):
            rows.append({"start": _fmt_ts(bucket.get("start_time")), "cost": "0.000000", "currency": currency})

    print("\nOpenAI API 费用")
    print(f"范围：{_fmt_ts(start)} -> {_fmt_ts(end)}，bucket={bucket_width}，group_by={group_by or '-'}")
    print(f"总费用：{total:.6f} {currency}")
    if rows:
        print_table(rows[-30:], ["start", "cost", "currency", "project", "api_key", "line_item"])
    else:
        print("没有查询到费用数据。")


def print_codex_usage(payload: dict[str, Any]) -> None:
    print("\nCodex 额度")
    print(f"账号：{payload.get('email') or '-'}")
    print(f"计划：{payload.get('plan_type') or '-'}")

    rate_limit = payload.get("rate_limit") or {}
    _print_rate_limit("主额度", rate_limit)

    code_review = payload.get("code_review_rate_limit")
    if code_review:
        _print_rate_limit("Code Review", code_review)

    additional = payload.get("additional_rate_limits") or []
    for item in additional:
        name = item.get("limit_name") or item.get("metered_feature") or "附加额度"
        _print_rate_limit(str(name), item.get("rate_limit") or {})

    credits = payload.get("credits") or {}
    reset_credits = payload.get("rate_limit_reset_credits") or {}
    print()
    print(f"重置券：{reset_credits.get('available_count', 0)}")
    print(
        "额外 credits："
        f"has={credits.get('has_credits', False)} "
        f"unlimited={credits.get('unlimited', False)} "
        f"balance={credits.get('balance', '-')}"
    )


def print_codex_reset_credits(payload: dict[str, Any]) -> None:
    credits = payload.get("credits") or []
    print("\n重置卡明细")
    if not credits:
        print("没有可显示的重置卡。")
        return

    rows: list[dict[str, Any]] = []
    for credit in credits:
        expires_at = credit.get("expires_at")
        granted_at = credit.get("granted_at")
        rows.append(
            {
                "status": credit.get("status") or "-",
                "title": credit.get("title") or "-",
                "granted": _fmt_iso(granted_at),
                "expires": _fmt_iso(expires_at),
                "remaining": _remaining_from_iso(expires_at),
            }
        )
    print_table(rows, ["status", "title", "granted", "expires", "remaining"])


def _print_rate_limit(title: str, rate_limit: dict[str, Any]) -> None:
    print()
    print(f"{title}：allowed={rate_limit.get('allowed')} limit_reached={rate_limit.get('limit_reached')}")
    for label, window in rate_limit_windows(rate_limit):
        print(f"  {label}：" + _format_window(window))


def _format_window(window: dict[str, Any]) -> str:
    used = window.get("used_percent")
    remaining = None
    if isinstance(used, (int, float)):
        remaining = max(0, 100 - used)
    reset_at = window.get("reset_at")
    reset = _fmt_ts(reset_at) if reset_at else "-"
    reset_after = window.get("reset_after_seconds")
    reset_after_text = _format_duration(reset_after) if isinstance(reset_after, (int, float)) else "-"
    if remaining is None:
        return f"已用 {used}% | 重置 {reset} | 剩余 {reset_after_text}"
    return f"已用 {used}% | 约剩 {remaining}% | 重置 {reset} | 剩余 {reset_after_text}"


def print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    widths = {
        column: max(
            len(column),
            *(len(str(row.get(column, ""))) for row in rows),
        )
        for column in columns
    }
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    print()
    print(header)
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))


def _fmt_ts(value: Any) -> str:
    if value is None:
        return "-"
    return datetime.fromtimestamp(int(value)).astimezone().strftime("%Y-%m-%d %H:%M")


def _fmt_iso(value: Any) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def _remaining_from_iso(value: Any) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "-"
    seconds = int((parsed.astimezone() - datetime.now().astimezone()).total_seconds())
    if seconds <= 0:
        return "已过期"
    return _format_duration(seconds)


def _format_duration(seconds: Any) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes or not parts:
        parts.append(f"{minutes}分钟")
    return "".join(parts)


def _usage_columns(usage_type: str) -> list[str]:
    if usage_type == "images":
        return ["start", "requests", "images", "model", "project", "api_key"]
    if usage_type == "audio_speeches":
        return ["start", "requests", "chars", "model", "project", "api_key"]
    if usage_type == "audio_transcriptions":
        return ["start", "requests", "seconds", "model", "project", "api_key"]
    if usage_type == "vector_stores":
        return ["start", "bytes", "project"]
    if usage_type == "code_interpreter_sessions":
        return ["start", "sessions", "project"]
    if usage_type == "file_searches":
        return ["start", "num_requests", "project", "api_key"]
    if usage_type == "web_searches":
        return ["start", "requests", "num_requests", "model", "project", "api_key"]
    return ["start", "requests", "input", "cached", "output", "model", "project", "api_key"]
