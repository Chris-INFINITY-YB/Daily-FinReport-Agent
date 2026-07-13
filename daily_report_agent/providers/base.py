"""未来数据提供方的只读 Protocol，不包含任何生产实现。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.security import Security


class QuoteProvider(Protocol):
    def fetch_quotes(self, securities: Sequence[Security]) -> list[MarketSnapshot]:
        """获取一组证券的标准行情快照。"""
        ...


class NewsProvider(Protocol):
    def fetch_news(
        self,
        security: Security,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[NewsItem]:
        """获取指定证券和时间窗口内的标准新闻。"""
        ...
