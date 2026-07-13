"""存储层无副作用序列化函数。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def datetime_to_utc_text(value: datetime) -> str:
    """将 aware datetime 规范化为带微秒的 UTC ISO-8601 文本。"""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime 必须带时区")
    normalized = value.astimezone(timezone.utc)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_text_to_datetime(value: str) -> datetime:
    """读取 ISO-8601 文本并返回 timezone-aware UTC datetime。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("UTC 时间文本不能为空")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"无效 UTC 时间文本: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("UTC 时间文本必须包含时区")
    return parsed.astimezone(timezone.utc)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
