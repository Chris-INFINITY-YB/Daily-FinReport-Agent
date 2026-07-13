from datetime import datetime, timezone

from daily_report_agent.datasource.base import NewsItem as LegacyNewsItem
from daily_report_agent.datasource.base import StockData
from daily_report_agent.ingestion.adapters import stockdata_to_collection
from daily_report_agent.ingestion.quality import QualityLevel, evaluate_collection_quality
from daily_report_agent.models.collection import CollectedSecurityData
from daily_report_agent.models.market import PriceWindow
from daily_report_agent.models.security import Security


FETCHED_AT = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


def test_news_without_market_can_continue() -> None:
    legacy = StockData(
        symbol="AAPL",
        market="us",
        name="苹果",
        news=[LegacyNewsItem(date="2026-07-13", headline="标题", summary="摘要")],
    )

    quality = evaluate_collection_quality(
        stockdata_to_collection(legacy, fetched_at=FETCHED_AT)
    )

    assert quality.has_news is True
    assert quality.has_market_data is False
    assert quality.can_continue is True
    assert quality.level is QualityLevel.HIGH


def test_market_without_news_can_continue() -> None:
    legacy = StockData(
        symbol="AAPL",
        market="us",
        name="苹果",
        start_price=98.0,
        end_price=100.0,
        pct_change=2.04,
    )

    quality = evaluate_collection_quality(
        stockdata_to_collection(legacy, fetched_at=FETCHED_AT)
    )

    assert quality.has_news is False
    assert quality.has_market_data is True
    assert quality.can_continue is True
    assert quality.level is QualityLevel.HIGH


def test_identity_only_fails_quality_gate() -> None:
    legacy = StockData(symbol="AAPL", market="us", name="苹果")

    quality = evaluate_collection_quality(
        stockdata_to_collection(legacy, fetched_at=FETCHED_AT)
    )

    assert quality.has_news is False
    assert quality.has_market_data is False
    assert quality.can_continue is False
    assert quality.level is QualityLevel.LOW


def test_empty_price_window_is_not_market_data() -> None:
    collection = CollectedSecurityData(
        security=Security(market="us", symbol="AAPL", name="苹果"),
        price_window=PriceWindow(),
    )

    quality = evaluate_collection_quality(collection)

    assert quality.has_market_data is False
    assert quality.can_continue is False
