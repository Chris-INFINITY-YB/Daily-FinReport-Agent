from contextlib import closing
from datetime import datetime, timezone

import pytest

from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.security import Security
from daily_report_agent.storage.database import Database, RepositoryError
from daily_report_agent.storage.repositories import (
    MarketSnapshotRepository,
    NewsRepository,
    SecurityRepository,
)


WHEN = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


def _news() -> NewsItem:
    return NewsItem(
        id="news-1",
        external_id="external-1",
        source="fixture",
        source_type="wire",
        title="固定新闻",
        summary="摘要",
        content=None,
        url=None,
        published_at=WHEN,
        fetched_at=WHEN,
        language="zh",
        content_hash="hash-1",
    )


def test_security_news_link_transaction_commits_together(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        security_id = SecurityRepository(connection).upsert_security(
            Security(market="us", symbol="AAPL", name="苹果")
        )
        news_repository = NewsRepository(connection)
        news_id, _ = news_repository.insert_or_get_news(_news())
        news_repository.link_to_security(news_id, security_id)

    with closing(storage_db.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM news_security_links"
        ).fetchone()[0] == 1


def test_invalid_link_rolls_back_security_insert(storage_db: Database) -> None:
    with pytest.raises(RepositoryError):
        with storage_db.transaction() as connection:
            security_id = SecurityRepository(connection).upsert_security(
                Security(market="us", symbol="AAPL", name="苹果")
            )
            NewsRepository(connection).link_to_security("missing-news", security_id)

    with closing(storage_db.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM news_security_links"
        ).fetchone()[0] == 0


def test_repeated_complete_transaction_remains_idempotent(storage_db: Database) -> None:
    ids = []
    for _ in range(2):
        with storage_db.transaction() as connection:
            security_id = SecurityRepository(connection).upsert_security(
                Security(market="us", symbol="AAPL", name="苹果")
            )
            news_repository = NewsRepository(connection)
            news_id, _ = news_repository.insert_or_get_news(_news())
            news_repository.link_to_security(news_id, security_id)
            snapshot_id, _ = MarketSnapshotRepository(
                connection
            ).insert_or_get_snapshot(
                security_id,
                MarketSnapshot(
                    symbol="AAPL",
                    observed_at=WHEN,
                    source="fixture",
                    price=200.0,
                    pct_change=0.0,
                ),
                WHEN,
            )
            ids.append((security_id, news_id, snapshot_id))

    with closing(storage_db.connect()) as connection:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "securities",
                "news_items",
                "news_security_links",
                "market_snapshots",
            )
        )

    assert ids[0] == ids[1]
    assert counts == (1, 1, 1, 1)
