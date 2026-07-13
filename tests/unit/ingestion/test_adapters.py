from datetime import datetime, timedelta, timezone

from daily_report_agent.datasource.base import NewsItem as LegacyNewsItem
from daily_report_agent.datasource.base import StockData
from daily_report_agent.ingestion.adapters import stockdata_to_collection
from daily_report_agent.models.issues import IssueCategory, IssueSeverity


FETCHED_AT = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


def test_stockdata_maps_to_security() -> None:
    legacy = StockData(symbol=" AAPL ", market=" us ", name=" 苹果 ")

    collection = stockdata_to_collection(legacy, fetched_at=FETCHED_AT)

    assert collection.security.symbol == "AAPL"
    assert collection.security.market == "us"
    assert collection.security.name == "苹果"
    assert collection.security.exchange is None


def test_legacy_news_hash_is_stable() -> None:
    news = [LegacyNewsItem(date="2026-07-13", headline="同一标题", summary="同一摘要")]
    legacy = StockData(symbol="AAPL", market="us", name="苹果", news=news)

    first = stockdata_to_collection(legacy, fetched_at=FETCHED_AT).news[0]
    second = stockdata_to_collection(
        legacy,
        fetched_at=FETCHED_AT + timedelta(hours=1),
    ).news[0]

    assert first.content_hash == second.content_hash
    assert first.id == second.id
    assert first.source == "legacy"
    assert first.source_type == "unknown"


def test_legacy_news_date_becomes_aware_and_records_precision_issue() -> None:
    legacy = StockData(
        symbol="AAPL",
        market="us",
        name="苹果",
        news=[LegacyNewsItem(date="2026-07-13", headline="标题", summary="")],
    )

    collection = stockdata_to_collection(legacy, fetched_at=FETCHED_AT)

    assert collection.news[0].published_at.utcoffset() is not None
    assert any(
        issue.operation == "normalize_news_time"
        and issue.category is IssueCategory.MISSING_DATA
        for issue in collection.issues
    )


def test_missing_market_does_not_create_zero_pct_change() -> None:
    legacy = StockData(symbol="AAPL", market="us", name="苹果")

    collection = stockdata_to_collection(legacy, fetched_at=FETCHED_AT)

    assert collection.price_window is None
    assert collection.market_snapshots == ()


def test_legacy_error_becomes_warning_issue() -> None:
    legacy = StockData(
        symbol="AAPL",
        market="us",
        name="苹果",
        error="新闻抓取失败",
    )

    collection = stockdata_to_collection(legacy, fetched_at=FETCHED_AT)
    issue = next(issue for issue in collection.issues if issue.message == "新闻抓取失败")

    assert issue.severity is IssueSeverity.WARNING
    assert issue.provider == "legacy"
