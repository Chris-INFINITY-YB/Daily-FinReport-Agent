from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from daily_report_agent.models.market import MarketSnapshot, PriceWindow
from daily_report_agent.models.security import Security
from daily_report_agent.storage.database import Database
from daily_report_agent.storage.repositories import (
    MarketSnapshotRepository,
    SecurityRepository,
)


OBSERVED = datetime(
    2026, 7, 13, 17, 0, 1, 456789, tzinfo=timezone(timedelta(hours=8))
)
FETCHED = datetime(2026, 7, 13, 9, 1, tzinfo=timezone.utc)


def _security_id(connection) -> int:
    return SecurityRepository(connection).upsert_security(
        Security(market="us", symbol="AAPL", name="苹果")
    )


def test_snapshot_insert_is_idempotent_and_time_round_trips(
    storage_db: Database,
) -> None:
    snapshot = MarketSnapshot(
        symbol="AAPL",
        observed_at=OBSERVED,
        source="fixture",
        price=200.0,
        pct_change=1.25,
    )
    with storage_db.transaction() as connection:
        repository = MarketSnapshotRepository(connection)
        security_id = _security_id(connection)
        first = repository.insert_or_get_snapshot(security_id, snapshot, FETCHED)
        second = repository.insert_or_get_snapshot(security_id, snapshot, FETCHED)

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM market_snapshots"
        ).fetchone()[0]
        stored = MarketSnapshotRepository(connection).get_by_id(first[0])

    assert first[1] is True
    assert second == (first[0], False)
    assert count == 1
    assert stored is not None
    assert stored.observed_at == OBSERVED


def test_none_is_sql_null_and_real_zero_stays_zero(storage_db: Database) -> None:
    snapshot = MarketSnapshot(
        symbol="AAPL",
        observed_at=OBSERVED,
        source="fixture",
        price=None,
        previous_close=0.0,
        pct_change=0.0,
        volume=None,
    )
    with storage_db.transaction() as connection:
        repository = MarketSnapshotRepository(connection)
        snapshot_id, _ = repository.insert_or_get_snapshot(
            _security_id(connection), snapshot, FETCHED
        )

    with closing(storage_db.connect()) as connection:
        row = connection.execute(
            "SELECT price, previous_close, pct_change, volume FROM market_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        restored = MarketSnapshotRepository(connection).get_by_id(snapshot_id)

    assert row["price"] is None
    assert row["volume"] is None
    assert row["previous_close"] == 0.0
    assert row["pct_change"] == 0.0
    assert restored is not None
    assert restored.price is None
    assert restored.pct_change == 0.0


def test_market_repository_rejects_price_window(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        repository = MarketSnapshotRepository(connection)
        security_id = _security_id(connection)
        with pytest.raises(TypeError, match="不接受 PriceWindow"):
            repository.insert_or_get_snapshot(  # type: ignore[arg-type]
                security_id,
                PriceWindow(100.0, 101.0, 1.0),
                FETCHED,
            )

    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM market_snapshots"
        ).fetchone()[0]
    assert count == 0
