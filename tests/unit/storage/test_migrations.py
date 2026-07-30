import sqlite3
from contextlib import closing
from importlib.resources import files

import pytest

from daily_report_agent.storage.database import Database
from daily_report_agent.storage.migrations import (
    Migration,
    MigrationError,
    apply_migrations,
)


EXPECTED_TABLES = {
    "schema_migrations",
    "securities",
    "security_aliases",
    "news_items",
    "news_security_links",
    "market_snapshots",
    "pipeline_runs",
    "provider_calls",
    "raw_responses",
    "provider_circuit_breakers",
}


def test_empty_database_upgrades_to_latest_schema(storage_db: Database) -> None:
    with closing(storage_db.connect()) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        migrations = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert EXPECTED_TABLES <= tables
    assert [tuple(row) for row in migrations] == [
        (1, "initial"),
        (2, "provider_circuit_breakers"),
    ]


def test_database_object_does_not_create_file_until_explicit_connect(tmp_path) -> None:
    path = tmp_path / "not-created.sqlite"

    Database(path)

    assert not path.exists()


def test_initialize_is_idempotent(storage_db: Database) -> None:
    storage_db.initialize()
    storage_db.initialize()

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]

    assert count == 2


def test_connection_enables_foreign_keys_and_file_wal(storage_db: Database) -> None:
    with closing(storage_db.connect()) as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert foreign_keys == 1
    assert journal_mode.lower() == "wal"


def test_required_indexes_exist(storage_db: Database) -> None:
    required = {
        "idx_securities_market_symbol",
        "idx_securities_exchange_symbol",
        "idx_securities_is_active",
        "uq_news_source_external_id",
        "uq_news_source_content_hash",
        "idx_market_snapshots_security_time",
        "idx_provider_circuit_breakers_state_open_until",
    }
    with closing(storage_db.connect()) as connection:
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert required <= indexes


def test_migrations_are_discovered_in_version_order_and_packaged() -> None:
    from daily_report_agent.storage.migrations import load_migrations

    migrations = load_migrations()
    resource = files("daily_report_agent.storage").joinpath(
        "sql/0002_provider_circuit_breakers.sql"
    )

    assert [(item.version, item.name) for item in migrations] == [
        (1, "initial"),
        (2, "provider_circuit_breakers"),
    ]
    assert resource.is_file()
    assert "CREATE TABLE provider_circuit_breakers" in resource.read_text(
        encoding="utf-8"
    )


def test_existing_0001_database_upgrades_without_losing_data(tmp_path) -> None:
    from daily_report_agent.storage.migrations import load_migrations

    database = Database(tmp_path / "upgrade.sqlite")
    initial = load_migrations()[0]
    with database.transaction() as connection:
        apply_migrations(connection, (initial,))
        connection.execute(
            """
            INSERT INTO securities(
                market, symbol, name, created_at, updated_at
            ) VALUES ('us', 'AAPL', 'Apple', 'created', 'updated')
            """
        )

    database.initialize()

    with closing(database.connect()) as connection:
        security = connection.execute(
            "SELECT market, symbol, name FROM securities"
        ).fetchone()
        migrations = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        circuit_count = connection.execute(
            "SELECT COUNT(*) FROM provider_circuit_breakers"
        ).fetchone()[0]

    assert tuple(security) == ("us", "AAPL", "Apple")
    assert [tuple(row) for row in migrations] == [
        (1, "initial"),
        (2, "provider_circuit_breakers"),
    ]
    assert circuit_count == 0


def test_failed_migration_rolls_back_all_schema_changes(tmp_path) -> None:
    database = Database(tmp_path / "broken.sqlite")
    broken = Migration(
        version=99,
        name="broken",
        sql="CREATE TABLE partial_table(id INTEGER);\nTHIS IS NOT SQL;\n",
    )

    with pytest.raises(MigrationError) as error:
        with database.transaction() as connection:
            apply_migrations(connection, (broken,))

    assert isinstance(error.value.__cause__, sqlite3.Error)
    with closing(database.connect()) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "partial_table" not in tables
    assert "schema_migrations" not in tables
