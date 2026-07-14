"""稳定、低频变化的证券资料模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .news import is_timezone_aware


_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class SecurityProfile:
    symbol: str
    market: str
    name: str | None
    exchange: str | None
    currency: str | None
    industry: str | None
    description: str | None
    source: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol 不能为空")
        if not isinstance(self.market, str) or self.market.strip().lower() not in {
            "cn",
            "us",
        }:
            raise ValueError("market 只允许 'cn' 或 'us'")
        if not is_timezone_aware(self.fetched_at):
            raise ValueError("fetched_at 必须是 timezone-aware datetime")

        if not isinstance(self.source, str):
            raise TypeError("source 必须是字符串")
        source = self.source.strip()
        if not source or _PROVIDER_ID_PATTERN.fullmatch(source) is None:
            raise ValueError("source 必须符合稳定 Provider ID 规则")

        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(self, "market", self.market.strip().lower())
        object.__setattr__(self, "source", source)

        for field_name in (
            "name",
            "exchange",
            "currency",
            "industry",
            "description",
        ):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, str):
                    raise TypeError(f"{field_name} 必须是字符串或 None")
                object.__setattr__(self, field_name, value.strip())
