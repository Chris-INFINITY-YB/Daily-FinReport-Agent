"""一次证券采集产生的标准化结果。"""

from __future__ import annotations

from dataclasses import dataclass

from .issues import DataIssue
from .market import MarketSnapshot, PriceWindow
from .news import NewsItem
from .security import Security


@dataclass(frozen=True, slots=True)
class CollectedSecurityData:
    security: Security
    profile_text: str = ""
    news: tuple[NewsItem, ...] = ()
    market_snapshots: tuple[MarketSnapshot, ...] = ()
    price_window: PriceWindow | None = None
    issues: tuple[DataIssue, ...] = ()
    raw_response_ids: tuple[int, ...] = ()
