"""分析层使用的不可变标准输入。"""

from __future__ import annotations

from dataclasses import dataclass

from .issues import DataIssue, IssueSeverity
from .market import MarketSnapshot, PriceWindow
from .news import NewsItem
from .security import Security


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


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    """不包含旧 DTO、LLM 结果、报告字段或数据库标识的分析输入。"""

    security: Security
    profile_text: str = ""
    news: tuple[NewsItem, ...] = ()
    price_window: PriceWindow | None = None
    market_snapshots: tuple[MarketSnapshot, ...] = ()
    issues: tuple[DataIssue, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("news", "market_snapshots", "issues"):
            if not isinstance(getattr(self, field_name), tuple):
                raise TypeError(f"{field_name} 必须是 tuple")

    @property
    def has_news(self) -> bool:
        return bool(self.news)

    @property
    def has_market_data(self) -> bool:
        return _price_window_has_data(self.price_window) or any(
            _snapshot_has_data(snapshot) for snapshot in self.market_snapshots
        )

    @property
    def blocking_issues(self) -> tuple[DataIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is IssueSeverity.ERROR
        )

    @property
    def warning_issues(self) -> tuple[DataIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is IssueSeverity.WARNING
        )
