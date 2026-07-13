"""标准新闻模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def is_timezone_aware(value: datetime) -> bool:
    """时间同时具有 tzinfo 和有效 UTC 偏移时才视为 timezone-aware。"""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


@dataclass(frozen=True, slots=True)
class NewsItem:
    id: str
    external_id: str | None
    source: str
    source_type: str
    title: str
    summary: str
    content: str | None
    url: str | None
    published_at: datetime
    fetched_at: datetime
    language: str
    content_hash: str
    related_symbols: tuple[str, ...] = ()
    source_reliability: float | None = None

    def __post_init__(self) -> None:
        required_text = {
            "id": self.id,
            "source": self.source,
            "source_type": self.source_type,
            "title": self.title,
            "language": self.language,
            "content_hash": self.content_hash,
        }
        for field_name, value in required_text.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 不能为空")

        if not is_timezone_aware(self.published_at):
            raise ValueError("published_at 必须是 timezone-aware datetime")
        if not is_timezone_aware(self.fetched_at):
            raise ValueError("fetched_at 必须是 timezone-aware datetime")
        if not isinstance(self.related_symbols, tuple):
            raise TypeError("related_symbols 必须是 tuple[str, ...]")
        if self.source_reliability is not None and not 0.0 <= self.source_reliability <= 1.0:
            raise ValueError("source_reliability 必须在 0 到 1 之间")

        for field_name, value in required_text.items():
            object.__setattr__(self, field_name, value.strip())
