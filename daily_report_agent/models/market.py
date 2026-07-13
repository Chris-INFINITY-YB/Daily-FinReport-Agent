"""标准行情快照及兼容区间模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .news import is_timezone_aware


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    observed_at: datetime
    source: str
    price: float | None = None
    previous_close: float | None = None
    pct_change: float | None = None
    volume: float | None = None
    amount: float | None = None
    turnover: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    market_cap: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip()
        source = self.source.strip()
        if not symbol:
            raise ValueError("symbol 不能为空")
        if not source:
            raise ValueError("source 不能为空")
        if not is_timezone_aware(self.observed_at):
            raise ValueError("observed_at 必须是 timezone-aware datetime")

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class PriceWindow:
    """保存多日区间结果，不与单点 MarketSnapshot 混用。"""

    start_price: float | None = None
    end_price: float | None = None
    period_pct_change: float | None = None
