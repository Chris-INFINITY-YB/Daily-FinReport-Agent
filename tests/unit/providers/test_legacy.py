from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from daily_report_agent.datasource.base import NewsItem as LegacyNewsItem
from daily_report_agent.datasource.base import StockData
from daily_report_agent.models.collection import CollectedSecurityData
from daily_report_agent.models.issues import IssueCategory, IssueSeverity
from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import ProviderUnavailableError
from daily_report_agent.providers.legacy import (
    LegacyDataSourceFacade,
    legacy_descriptor,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


@dataclass
class CountingDataSource:
    result: StockData
    calls: list[tuple[str, str, int, int]] = field(default_factory=list)

    def fetch(
        self,
        symbol: str,
        name: str,
        news_days: int,
        max_news: int,
    ) -> StockData:
        self.calls.append((symbol, name, news_days, max_news))
        return self.result


def stock_data() -> StockData:
    return StockData(
        symbol="600519",
        name="贵州茅台",
        market="cn",
        intro="公司简介",
        start_date="2026-07-07",
        end_date="2026-07-14",
        start_price=1400.0,
        end_price=1450.0,
        pct_change=3.57,
        news=[
            LegacyNewsItem(
                date="2026-07-13",
                headline="离线新闻",
                summary="摘要",
            )
        ],
        error="旧链路部分失败",
    )


def test_legacy_descriptor_uses_mixed_legacy_identity() -> None:
    value = legacy_descriptor("cn")
    assert value.provider_id == "legacy-cn-datasource"
    assert value.markets == frozenset({"cn"})
    assert {item.value for item in value.capabilities} == {
        "quote",
        "news",
        "profile",
    }


def test_facade_calls_fetch_once_and_reuses_existing_adapter() -> None:
    source = CountingDataSource(stock_data())
    security = Security(market="cn", symbol="600519", name="贵州茅台")
    facade = LegacyDataSourceFacade(
        source=source,
        descriptor=legacy_descriptor("cn"),
        news_days=5,
        max_news=8,
    )

    result = facade.collect(security, fetched_at=NOW)

    assert source.calls == [("600519", "贵州茅台", 5, 8)]
    assert isinstance(result, CollectedSecurityData)
    assert result.security == security
    assert result.profile_text == "公司简介"
    assert len(result.news) == 1
    assert result.news[0].title == "离线新闻"
    assert result.news[0].related_symbols == ("600519",)
    assert security == Security(market="cn", symbol="600519", name="贵州茅台")


def test_facade_preserves_price_window_without_fabricating_snapshot() -> None:
    result = LegacyDataSourceFacade(
        source=CountingDataSource(stock_data()),
        descriptor=legacy_descriptor("cn"),
    ).collect(Security(market="cn", symbol="600519", name="贵州茅台"), fetched_at=NOW)

    assert result.price_window is not None
    assert result.price_window.start_price == 1400.0
    assert result.price_window.end_price == 1450.0
    assert result.price_window.period_pct_change == 3.57
    assert result.market_snapshots == ()


def test_facade_keeps_existing_legacy_error_warning_semantics() -> None:
    result = LegacyDataSourceFacade(
        source=CountingDataSource(stock_data()),
        descriptor=legacy_descriptor("cn"),
    ).collect(Security(market="cn", symbol="600519", name="贵州茅台"), fetched_at=NOW)

    error_issue = next(issue for issue in result.issues if issue.message == "旧链路部分失败")
    assert error_issue.severity is IssueSeverity.WARNING
    assert error_issue.category is IssueCategory.UNKNOWN
    assert error_issue.operation == "fetch"


class ExplodingDataSource:
    def fetch(self, symbol: str, name: str, news_days: int, max_news: int) -> StockData:
        raise RuntimeError(
            "Bearer private-token https://legacy.test/data?api_key=secret"
        )


def test_facade_wraps_fetch_failure_without_leaking_cause() -> None:
    facade = LegacyDataSourceFacade(
        source=ExplodingDataSource(),
        descriptor=legacy_descriptor("us"),
    )
    with pytest.raises(ProviderUnavailableError) as caught:
        facade.collect(Security(market="us", symbol="AAPL", name="Apple"))

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert caught.value.provider_id == "legacy-us-datasource"
    assert caught.value.operation == "fetch"
    assert caught.value.retryable is True
    assert "private-token" not in caught.value.safe_message
    assert "https://" not in caught.value.safe_message


def test_facade_has_no_storage_or_production_side_effect_dependencies() -> None:
    import daily_report_agent.providers.legacy as legacy_module

    names = set(vars(legacy_module))
    assert "Database" not in names
    assert "Repository" not in names
    assert "Analyzer" not in names
    assert "Report" not in names
    assert "Notifier" not in names


def test_facade_rejects_market_outside_descriptor() -> None:
    facade = LegacyDataSourceFacade(
        source=CountingDataSource(stock_data()),
        descriptor=legacy_descriptor("us"),
    )
    with pytest.raises(ValueError, match="descriptor.markets"):
        facade.collect(Security(market="cn", symbol="600519", name="贵州茅台"))
