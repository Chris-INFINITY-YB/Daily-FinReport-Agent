"""未来数据提供方的同步只读 Protocol，不包含任何生产实现。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.profile import SecurityProfile
from daily_report_agent.models.security import Security

from .contracts import ProviderDescriptor, ProviderResult


class QuoteProvider(Protocol):
    descriptor: ProviderDescriptor

    def fetch_quotes(
        self, securities: tuple[Security, ...]
    ) -> ProviderResult[MarketSnapshot]:
        """获取单点行情快照；不得用来表示多日 PriceWindow。"""
        ...


class NewsProvider(Protocol):
    descriptor: ProviderDescriptor

    def fetch_news(
        self,
        security: Security,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> ProviderResult[NewsItem]:
        """获取指定证券和时间窗口内的标准新闻。"""
        ...


class ProfileProvider(Protocol):
    descriptor: ProviderDescriptor

    def fetch_profile(
        self, security: Security
    ) -> ProviderResult[SecurityProfile]:
        """获取稳定公司资料；无资料时返回空 items。"""
        ...
