from contextlib import closing
from datetime import datetime, timezone

from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.security import Security
from daily_report_agent.storage.database import Database
from daily_report_agent.storage.repositories import NewsRepository, SecurityRepository


WHEN = datetime(2026, 7, 13, 9, 30, 1, 123456, tzinfo=timezone.utc)


def make_news(
    *,
    news_id: str = "news-1",
    source: str = "fixture",
    external_id: str | None = "external-1",
    content_hash: str = "hash-1",
) -> NewsItem:
    return NewsItem(
        id=news_id,
        external_id=external_id,
        source=source,
        source_type="wire",
        title="固定标题",
        summary="固定摘要",
        content="已脱敏正文",
        url="https://example.invalid/news/1",
        published_at=WHEN,
        fetched_at=WHEN,
        language="zh",
        content_hash=content_hash,
        source_reliability=0.8,
    )


def test_external_id_identity_returns_existing_database_id(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        repository = NewsRepository(connection)
        first = repository.insert_or_get_news(make_news(news_id="db-id"))
        second = repository.insert_or_get_news(make_news(news_id="different-model-id"))

    with closing(storage_db.connect()) as connection:
        count = connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]

    assert first == ("db-id", True)
    assert second == ("db-id", False)
    assert count == 1


def test_content_hash_identity_without_external_id_is_idempotent(
    storage_db: Database,
) -> None:
    with storage_db.transaction() as connection:
        repository = NewsRepository(connection)
        first = repository.insert_or_get_news(
            make_news(news_id="hash-id", external_id=None)
        )
        second = repository.insert_or_get_news(
            make_news(news_id="other-id", external_id=None)
        )

    assert first == ("hash-id", True)
    assert second == ("hash-id", False)


def test_same_content_hash_from_different_sources_can_coexist(
    storage_db: Database,
) -> None:
    with storage_db.transaction() as connection:
        repository = NewsRepository(connection)
        repository.insert_or_get_news(
            make_news(news_id="a", source="source-a", external_id=None)
        )
        repository.insert_or_get_news(
            make_news(news_id="b", source="source-b", external_id=None)
        )

    with closing(storage_db.connect()) as connection:
        count = connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
    assert count == 2


def test_link_is_idempotent_and_read_returns_standard_news(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        security_repository = SecurityRepository(connection)
        news_repository = NewsRepository(connection)
        security_id = security_repository.upsert_security(
            Security(market="us", symbol="AAPL", name="苹果")
        )
        news_id, _ = news_repository.insert_or_get_news(make_news())
        news_repository.link_to_security(news_id, security_id, confidence=0.9)
        news_repository.link_to_security(news_id, security_id, confidence=0.9)

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM news_security_links"
        ).fetchone()[0]
        stored = NewsRepository(connection).get_by_id("news-1")

    assert count == 1
    assert stored is not None
    assert stored.related_symbols == ("AAPL",)
    assert stored.published_at == WHEN
    assert stored.source_reliability == 0.8


def test_legacy_source_remains_legacy(storage_db: Database) -> None:
    news = make_news(source="legacy", external_id=None)
    with storage_db.transaction() as connection:
        news_id, _ = NewsRepository(connection).insert_or_get_news(news)

    with closing(storage_db.connect()) as connection:
        stored = NewsRepository(connection).get_by_id(news_id)

    assert stored is not None
    assert stored.source == "legacy"
