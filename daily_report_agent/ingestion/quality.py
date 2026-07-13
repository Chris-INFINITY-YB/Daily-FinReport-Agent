"""标准采集结果的独立质量评估，不依赖 Analyzer。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from daily_report_agent.models.collection import CollectedSecurityData
from daily_report_agent.models.market import MarketSnapshot, PriceWindow


class QualityLevel(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class CollectionQuality:
    level: QualityLevel
    can_analyze: bool
    has_news: bool
    has_market_data: bool

    @property
    def can_continue(self) -> bool:
        return self.can_analyze


def _snapshot_has_data(snapshot: MarketSnapshot) -> bool:
    return any(
        value is not None
        for value in (
            snapshot.price,
            snapshot.previous_close,
            snapshot.pct_change,
            snapshot.volume,
            snapshot.amount,
            snapshot.turnover,
            snapshot.pe_ttm,
            snapshot.pb,
            snapshot.market_cap,
        )
    )


def _price_window_has_data(window: PriceWindow | None) -> bool:
    return window is not None and any(
        value is not None
        for value in (
            window.start_price,
            window.end_price,
            window.period_pct_change,
        )
    )


def evaluate_collection_quality(
    collection: CollectedSecurityData,
) -> CollectionQuality:
    has_news = bool(collection.news)
    has_market_data = _price_window_has_data(collection.price_window) or any(
        _snapshot_has_data(snapshot) for snapshot in collection.market_snapshots
    )
    can_analyze = has_news or has_market_data
    return CollectionQuality(
        level=QualityLevel.HIGH if can_analyze else QualityLevel.LOW,
        can_analyze=can_analyze,
        has_news=has_news,
        has_market_data=has_market_data,
    )
