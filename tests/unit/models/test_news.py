from datetime import datetime, timezone

import pytest

from daily_report_agent.models.news import NewsItem, is_timezone_aware


def make_news(**overrides) -> NewsItem:
    values = {
        "id": "news-1",
        "external_id": None,
        "source": "fixture",
        "source_type": "news",
        "title": "测试新闻",
        "summary": "测试摘要",
        "content": None,
        "url": None,
        "published_at": datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        "fetched_at": datetime(2026, 7, 13, 8, 5, tzinfo=timezone.utc),
        "language": "zh",
        "content_hash": "abc123",
    }
    values.update(overrides)
    return NewsItem(**values)


def test_news_item_accepts_timezone_aware_times() -> None:
    item = make_news()

    assert is_timezone_aware(item.published_at)
    assert is_timezone_aware(item.fetched_at)
    assert item.related_symbols == ()


@pytest.mark.parametrize("field_name", ["published_at", "fetched_at"])
def test_news_item_rejects_naive_datetime(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_news(**{field_name: datetime(2026, 7, 13, 8, 0)})


@pytest.mark.parametrize("field_name", ["title", "content_hash"])
def test_news_item_requires_title_and_content_hash(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_news(**{field_name: "   "})
