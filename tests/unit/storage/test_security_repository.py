from contextlib import closing

from daily_report_agent.models.security import Security
from daily_report_agent.storage.database import Database
from daily_report_agent.storage.repositories import SecurityRepository


def test_security_upsert_is_idempotent_and_updates_nonempty_fields(
    storage_db: Database,
) -> None:
    with storage_db.transaction() as connection:
        repository = SecurityRepository(connection)
        first_id = repository.upsert_security(
            Security(market="us", symbol="AAPL", name="苹果", exchange="NASDAQ")
        )
        second_id = repository.upsert_security(
            Security(
                market="us",
                symbol="AAPL",
                name="苹果公司",
                exchange=None,
                currency="USD",
                industry="Technology",
            )
        )

    with closing(storage_db.connect()) as connection:
        count = connection.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
        stored = SecurityRepository(connection).get_by_market_symbol("us", "AAPL")

    assert first_id == second_id
    assert count == 1
    assert stored == Security(
        market="us",
        symbol="AAPL",
        name="苹果公司",
        exchange="NASDAQ",
        currency="USD",
        industry="Technology",
    )


def test_none_does_not_overwrite_existing_optional_security_fields(
    storage_db: Database,
) -> None:
    with storage_db.transaction() as connection:
        repository = SecurityRepository(connection)
        repository.upsert_security(
            Security(
                market="cn",
                symbol="600519",
                name="贵州茅台",
                exchange="SSE",
                currency="CNY",
                industry="白酒",
            )
        )
        repository.upsert_security(
            Security(market="cn", symbol="600519", name="茅台", exchange=None)
        )

    with closing(storage_db.connect()) as connection:
        stored = SecurityRepository(connection).get_by_market_symbol("cn", "600519")

    assert stored is not None
    assert stored.exchange == "SSE"
    assert stored.currency == "CNY"
    assert stored.industry == "白酒"


def test_alias_replacement_and_read_are_idempotent(storage_db: Database) -> None:
    aliases = ("Apple", "苹果电脑")
    with storage_db.transaction() as connection:
        repository = SecurityRepository(connection)
        security_id = repository.upsert_security(
            Security(market="us", symbol="AAPL", name="苹果")
        )
        repository.replace_aliases(security_id, aliases)
        repository.replace_aliases(security_id, aliases)

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM security_aliases"
        ).fetchone()[0]
        stored = SecurityRepository(connection).get_by_market_symbol("us", "AAPL")

    assert count == 2
    assert stored is not None
    assert stored.aliases == tuple(sorted(aliases))
