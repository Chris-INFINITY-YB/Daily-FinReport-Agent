from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from daily_report_agent.models.security import Security
from daily_report_agent.providers.eastmoney.news import EastmoneyNewsProvider
from daily_report_agent.providers.errors import ProviderTimeoutError
from daily_report_agent.storage.database import Database, RepositoryError
from daily_report_agent.storage.repositories import NewsRepository, SecurityRepository


ROOT = Path(__file__).parents[3]
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "providers"
    / "eastmoney"
    / "news_synthetic_multiple.json"
)
SECURITY = Security(market="cn", symbol="123456", name="Synthetic Security")
FIRST_FETCHED_AT = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
SECOND_FETCHED_AT = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 7, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 7, 31, tzinfo=timezone.utc)
TABLES = (
    "securities",
    "news_items",
    "news_security_links",
    "raw_responses",
)


def _fixture_rows() -> tuple[MappingProxyType[str, object], ...]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return tuple(MappingProxyType(row) for row in payload)


def _replay_rows_with_different_urls() -> tuple[MappingProxyType[str, object], ...]:
    return tuple(
        MappingProxyType(
            {
                **row,
                "新闻链接": f"https://example.invalid/replay/{index}",
            }
        )
        for index, row in enumerate(_fixture_rows())
    )


@dataclass
class FixtureTransport:
    rows: tuple[MappingProxyType[str, object], ...]
    calls: list[str] = field(default_factory=list)

    def fetch_news_rows(self, symbol: str):
        self.calls.append(symbol)
        return self.rows


@dataclass
class FailingTransport:
    calls: list[str] = field(default_factory=list)

    def fetch_news_rows(self, symbol: str):
        self.calls.append(symbol)
        raise TimeoutError("synthetic timeout")


def _fetch(provider: EastmoneyNewsProvider):
    return provider.fetch_news(SECURITY, SINCE, UNTIL, 10)


def _store_result(database: Database, result):
    stored = []
    with database.transaction() as connection:
        security_id = SecurityRepository(connection).upsert_security(SECURITY)
        repository = NewsRepository(connection)
        for item in result.items:
            news_id, inserted = repository.insert_or_get_news(item)
            repository.link_to_security(news_id, security_id)
            repository.link_to_security(news_id, security_id)
            stored.append((news_id, inserted))
    return security_id, tuple(stored)


def _fetch_then_store(database: Database, provider: EastmoneyNewsProvider):
    result = _fetch(provider)
    return result, _store_result(database, result)


def _table_counts(database: Database) -> tuple[int, ...]:
    with closing(database.connect()) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        )


def test_provider_result_round_trips_through_existing_news_repositories(
    storage_db: Database,
) -> None:
    transport = FixtureTransport(_fixture_rows())
    result, (security_id, stored_results) = _fetch_then_store(
        storage_db,
        EastmoneyNewsProvider(transport, clock=lambda: FIRST_FETCHED_AT),
    )

    assert transport.calls == ["123456"]
    assert len(result.items) == 5
    assert len(result.issues) == 3
    assert stored_results == tuple((item.id, True) for item in result.items)
    assert _table_counts(storage_db) == (1, 5, 5, 0)

    with closing(storage_db.connect()) as connection:
        rows = connection.execute(
            """
            SELECT id, external_id, source, source_type, content, language,
                   source_reliability, raw_response_id
            FROM news_items
            """
        ).fetchall()
        links = connection.execute(
            """
            SELECT links.relation_type, links.confidence, securities.id,
                   securities.market, securities.symbol
            FROM news_security_links AS links
            JOIN securities ON securities.id = links.security_id
            ORDER BY links.news_id
            """
        ).fetchall()
        repository = NewsRepository(connection)
        round_tripped = tuple(repository.get_by_id(item.id) for item in result.items)

    assert all(row[1:] == (None, "eastmoney", "news", None, "zh", None, None) for row in rows)
    assert [tuple(row) for row in links] == [
        ("mentioned", None, security_id, "cn", "123456")
    ] * 5
    assert all(item is not None for item in round_tripped)
    for original, stored in zip(result.items, round_tripped, strict=True):
        assert stored is not None
        assert stored.id == original.id
        assert stored.title == original.title
        assert stored.summary == original.summary
        assert stored.url == original.url
        assert stored.content_hash == original.content_hash
        assert stored.related_symbols == ("123456",)
        assert stored.published_at == original.published_at
        assert stored.published_at.tzinfo is timezone.utc
        assert stored.fetched_at == FIRST_FETCHED_AT
        assert stored.fetched_at.tzinfo is timezone.utc


def test_repeated_provider_content_and_links_are_idempotent_without_updates(
    storage_db: Database,
) -> None:
    first_result, (_, first_stored) = _fetch_then_store(
        storage_db,
        EastmoneyNewsProvider(
            FixtureTransport(_fixture_rows()), clock=lambda: FIRST_FETCHED_AT
        ),
    )
    second_result, (_, second_stored) = _fetch_then_store(
        storage_db,
        EastmoneyNewsProvider(
            FixtureTransport(_replay_rows_with_different_urls()),
            clock=lambda: SECOND_FETCHED_AT,
        ),
    )

    assert [item.id for item in second_result.items] == [
        item.id for item in first_result.items
    ]
    assert first_stored == tuple((item.id, True) for item in first_result.items)
    assert second_stored == tuple((item.id, False) for item in first_result.items)
    assert _table_counts(storage_db) == (1, 5, 5, 0)

    with closing(storage_db.connect()) as connection:
        repository = NewsRepository(connection)
        stored = tuple(repository.get_by_id(item.id) for item in first_result.items)

    for first, second, existing in zip(
        first_result.items, second_result.items, stored, strict=True
    ):
        assert existing is not None
        assert second.fetched_at == SECOND_FETCHED_AT
        assert second.url != first.url
        assert existing.fetched_at == FIRST_FETCHED_AT
        assert existing.url == first.url


def test_provider_failure_before_transaction_leaves_storage_empty(
    storage_db: Database,
) -> None:
    transport = FailingTransport()
    provider = EastmoneyNewsProvider(transport, clock=lambda: FIRST_FETCHED_AT)

    with pytest.raises(ProviderTimeoutError):
        _fetch_then_store(storage_db, provider)

    assert transport.calls == ["123456"]
    assert _table_counts(storage_db) == (0, 0, 0, 0)


def test_later_repository_failure_rolls_back_security_news_and_link(
    storage_db: Database,
) -> None:
    result = _fetch(
        EastmoneyNewsProvider(
            FixtureTransport(_fixture_rows()), clock=lambda: FIRST_FETCHED_AT
        )
    )

    with pytest.raises(RepositoryError):
        with storage_db.transaction() as connection:
            security_id = SecurityRepository(connection).upsert_security(SECURITY)
            repository = NewsRepository(connection)
            news_id, inserted = repository.insert_or_get_news(result.items[0])
            assert inserted is True
            repository.link_to_security(news_id, security_id)
            repository.link_to_security("missing-news-id", security_id)

    assert _table_counts(storage_db) == (0, 0, 0, 0)


def test_existing_schema_enforces_news_identity_and_link_foreign_keys(
    storage_db: Database,
) -> None:
    with closing(storage_db.connect()) as connection:
        index_names = {
            row[1] for row in connection.execute("PRAGMA index_list(news_items)")
        }
        news_columns = {
            row[1]: {"not_null": row[3], "primary_key": row[5]}
            for row in connection.execute("PRAGMA table_info(news_items)")
        }
        link_columns = {
            row[1]: row[5]
            for row in connection.execute("PRAGMA table_info(news_security_links)")
        }
        link_targets = {
            row[2]
            for row in connection.execute(
                "PRAGMA foreign_key_list(news_security_links)"
            )
        }
        foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert foreign_keys_enabled == 1
    assert {
        "uq_news_source_external_id",
        "uq_news_source_content_hash",
    } <= index_names
    assert news_columns["id"]["primary_key"] == 1
    assert news_columns["external_id"]["not_null"] == 0
    assert news_columns["raw_response_id"]["not_null"] == 0
    assert link_columns["news_id"] == 1
    assert link_columns["security_id"] == 2
    assert link_targets == {"news_items", "securities"}
