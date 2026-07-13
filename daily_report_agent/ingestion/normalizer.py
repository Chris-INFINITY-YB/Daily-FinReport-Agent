"""无网络、无文件访问的标准化纯函数。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime, time, timezone, tzinfo

from daily_report_agent.models.news import NewsItem


_ZERO_AS_MISSING_FIELDS = {
    "pct_change",
    "volume",
    "amount",
    "turnover",
    "pe_ttm",
    "pb",
    "market_cap",
}
_EXPLICIT_TIME_RE = re.compile(r"(?:T|\s)\d{1,2}:\d{2}")


def normalize_symbol(symbol: str) -> str:
    """只清理空白，不添加或删除任何数据源前缀。"""
    if not isinstance(symbol, str):
        raise TypeError("symbol 必须是字符串")
    normalized = symbol.strip()
    if not normalized:
        raise ValueError("symbol 不能为空")
    return normalized


def normalize_datetime(
    value: str | date | datetime | None,
    *,
    default_timezone: tzinfo = timezone.utc,
    fallback: datetime | None = None,
) -> datetime:
    """把常见旧时间值转为带时区 datetime；无法解析时可使用显式 fallback。"""
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, time.min)
        elif isinstance(value, str) and value.strip():
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        else:
            raise ValueError("时间值为空")
    except (TypeError, ValueError) as exc:
        if fallback is None:
            raise ValueError(f"无法解析时间: {value!r}") from exc
        return normalize_datetime(fallback, default_timezone=default_timezone)

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed


def has_explicit_time(value: str | date | datetime | None) -> bool:
    """判断原始值是否明确提供了时分信息。"""
    if isinstance(value, datetime):
        return True
    if isinstance(value, date):
        return False
    return isinstance(value, str) and bool(_EXPLICIT_TIME_RE.search(value.strip()))


def _normalize_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def _stable_content_hash(title: str, summary: str) -> str:
    payload = f"{title}\n{summary}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_news(
    *,
    headline: object,
    summary: object,
    published_at: str | date | datetime | None,
    fetched_at: datetime,
    symbol: str,
) -> NewsItem:
    """将旧新闻字段转换为标准 NewsItem，不猜测真实来源。"""
    title = _normalize_text(headline)
    normalized_summary = _normalize_text(summary)
    normalized_fetched_at = normalize_datetime(fetched_at)
    normalized_published_at = normalize_datetime(
        published_at,
        fallback=normalized_fetched_at,
    )
    normalized_symbol = normalize_symbol(symbol)
    content_hash = _stable_content_hash(title, normalized_summary)

    return NewsItem(
        id=f"legacy-{content_hash}",
        external_id=None,
        source="legacy",
        source_type="unknown",
        title=title,
        summary=normalized_summary,
        content=None,
        url=None,
        published_at=normalized_published_at,
        fetched_at=normalized_fetched_at,
        language="und",
        content_hash=content_hash,
        related_symbols=(normalized_symbol,),
        source_reliability=None,
    )


def zero_to_none(value: object, field_name: str) -> object:
    """只在明确允许的可选指标中把旧零值哨兵转换为 None。"""
    if (
        field_name in _ZERO_AS_MISSING_FIELDS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == 0
    ):
        return None
    return value


def normalize_missing_values(values: Mapping[str, object]) -> dict[str, object]:
    """按字段白名单处理旧零值；price/start_price/end_price 永远不会被转换。"""
    return {name: zero_to_none(value, name) for name, value in values.items()}
