from contextlib import closing
from datetime import datetime, timezone

import pytest
import sqlite3

from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.security import Security
from daily_report_agent.storage.database import (
    Database,
    RepositoryError,
    StorageError,
)
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


def test_immediate_transaction_commits(storage_db: Database) -> None:
    with storage_db.immediate_transaction() as connection:
        connection.execute(
            """
            INSERT INTO provider_circuit_breakers(
                provider_id, operation, state, consecutive_failures,
                last_transition_at, half_open_probe_active, version, updated_at
            ) VALUES ('fixture', 'fetch', 'closed', 0, 'time', 0, 0, 'time')
            """
        )

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM provider_circuit_breakers"
        ).fetchone()[0]
    assert count == 1


def test_immediate_transaction_rolls_back_sqlite_error(
    storage_db: Database,
) -> None:
    with pytest.raises(StorageError, match="事务执行失败"):
        with storage_db.immediate_transaction() as connection:
            connection.execute(
                """
                INSERT INTO provider_circuit_breakers(
                    provider_id, operation, state, consecutive_failures,
                    last_transition_at, half_open_probe_active, version,
                    updated_at
                ) VALUES ('fixture', 'fetch', 'closed', 0, 'time', 0, 0, 'time')
                """
            )
            connection.execute("THIS IS NOT SQL")

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM provider_circuit_breakers"
        ).fetchone()[0]
    assert count == 0


def test_immediate_transaction_rolls_back_regular_exception(
    storage_db: Database,
) -> None:
    with pytest.raises(RuntimeError, match="force rollback"):
        with storage_db.immediate_transaction() as connection:
            connection.execute(
                """
                INSERT INTO provider_circuit_breakers(
                    provider_id, operation, state, consecutive_failures,
                    last_transition_at, half_open_probe_active, version,
                    updated_at
                ) VALUES ('fixture', 'fetch', 'closed', 0, 'time', 0, 0, 'time')
                """
            )
            raise RuntimeError("force rollback")

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM provider_circuit_breakers"
        ).fetchone()[0]
    assert count == 0


class _TrackingConnection(sqlite3.Connection):
    closed = False
    rolled_back = False
    fail_commit = False

    def commit(self) -> None:
        if self.fail_commit:
            raise sqlite3.OperationalError("unsafe commit details")
        super().commit()

    def rollback(self) -> None:
        self.rolled_back = True
        super().rollback()

    def close(self) -> None:
        self.closed = True
        super().close()


def test_immediate_transaction_closes_connection(monkeypatch, tmp_path) -> None:
    database = Database(tmp_path / "unused.sqlite")
    connection = sqlite3.connect(":memory:", factory=_TrackingConnection)
    monkeypatch.setattr(database, "connect", lambda: connection)

    with database.immediate_transaction():
        pass

    assert connection.closed is True


def test_immediate_commit_failure_rolls_back_and_closes(
    monkeypatch,
    tmp_path,
) -> None:
    database = Database(tmp_path / "unused.sqlite")
    connection = sqlite3.connect(":memory:", factory=_TrackingConnection)
    connection.fail_commit = True
    monkeypatch.setattr(database, "connect", lambda: connection)

    with pytest.raises(StorageError, match="事务提交失败"):
        with database.immediate_transaction():
            pass

    assert connection.rolled_back is True
    assert connection.closed is True
