from datetime import datetime, timezone

from daily_report_agent.datasource.base import NewsItem as LegacyNewsItem
from daily_report_agent.datasource.base import StockData
from daily_report_agent.ingestion.adapters import (
    collection_to_analysis_input,
    stockdata_to_analysis_input,
    stockdata_to_collection,
)


FETCHED_AT = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


def test_collection_to_analysis_input_preserves_standard_fields() -> None:
    legacy = StockData(
        symbol="AAPL",
        name="苹果",
        market="us",
        intro="公司简介",
        start_price=200.0,
        end_price=204.0,
        pct_change=2.0,
        news=[LegacyNewsItem(date="2026-07-12", headline="标题")],
        error="部分抓取失败",
    )
    collection = stockdata_to_collection(legacy, fetched_at=FETCHED_AT)

    analysis_input = collection_to_analysis_input(collection)

    assert analysis_input.security is collection.security
    assert analysis_input.profile_text == collection.profile_text
    assert analysis_input.news is collection.news
    assert analysis_input.price_window is collection.price_window
    assert analysis_input.market_snapshots is collection.market_snapshots
    assert analysis_input.issues is collection.issues


def test_stockdata_to_analysis_input_reuses_collection_conversion() -> None:
    legacy = StockData(symbol="AAPL", name="苹果", market="us")

    direct = stockdata_to_analysis_input(legacy, fetched_at=FETCHED_AT)
    via_collection = collection_to_analysis_input(
        stockdata_to_collection(legacy, fetched_at=FETCHED_AT)
    )

    assert direct == via_collection
