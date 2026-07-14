"""旧 DataSource 到标准采集结果的离线旁路 Façade。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from daily_report_agent.datasource.base import StockData
from daily_report_agent.ingestion.adapters import stockdata_to_collection
from daily_report_agent.models.collection import CollectedSecurityData
from daily_report_agent.models.security import Security

from .contracts import ProviderCapability, ProviderDescriptor
from .errors import ProviderUnavailableError


class LegacyDataSource(Protocol):
    """与当前 DataSource.fetch 保持一致的最小依赖协议。"""

    def fetch(
        self,
        symbol: str,
        name: str,
        news_days: int,
        max_news: int,
    ) -> StockData:
        ...


def legacy_descriptor(market: str) -> ProviderDescriptor:
    """构造混合旧链路自身的 Descriptor，不冒充底层真实 Provider。"""
    normalized_market = market.strip().lower()
    if normalized_market not in {"cn", "us"}:
        raise ValueError("market 只允许 'cn' 或 'us'")
    return ProviderDescriptor(
        provider_id=f"legacy-{normalized_market}-datasource",
        display_name=f"Legacy {normalized_market.upper()} DataSource",
        capabilities=frozenset(
            {
                ProviderCapability.QUOTE,
                ProviderCapability.NEWS,
                ProviderCapability.PROFILE,
            }
        ),
        markets=frozenset({normalized_market}),
    )


@dataclass(frozen=True, slots=True)
class LegacyDataSourceFacade:
    source: LegacyDataSource
    descriptor: ProviderDescriptor
    news_days: int = 7
    max_news: int = 15

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, ProviderDescriptor):
            raise TypeError("descriptor 必须是 ProviderDescriptor")
        if (
            not isinstance(self.news_days, int)
            or isinstance(self.news_days, bool)
            or self.news_days <= 0
        ):
            raise ValueError("news_days 必须是正整数")
        if (
            not isinstance(self.max_news, int)
            or isinstance(self.max_news, bool)
            or self.max_news <= 0
        ):
            raise ValueError("max_news 必须是正整数")

    def collect(
        self,
        security: Security,
        *,
        fetched_at: datetime | None = None,
    ) -> CollectedSecurityData:
        """调用旧 fetch 一次并复用既有适配器；保留 PriceWindow 语义。"""
        if security.market not in self.descriptor.markets:
            raise ValueError("security.market 不在 descriptor.markets 中")
        try:
            legacy_data = self.source.fetch(
                security.symbol,
                security.name,
                self.news_days,
                self.max_news,
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                provider_id=self.descriptor.provider_id,
                operation="fetch",
                safe_message="Legacy DataSource fetch failed",
                code="legacy_fetch_failed",
            ) from exc
        return stockdata_to_collection(legacy_data, fetched_at=fetched_at)
