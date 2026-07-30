from __future__ import annotations

from contextlib import closing
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from daily_report_agent.providers.circuit_breaker import (
    CircuitBreakerKey,
    CircuitBreakerPolicy,
    CircuitBreakerState,
    CircuitOutcome,
    evaluate_circuit,
    record_circuit_outcome,
)
from daily_report_agent.providers.routing import RetryErrorCode
from daily_report_agent.storage.circuit_breaker import (
    CircuitBreakerRepository,
    CircuitStorageError,
    CircuitStorageErrorCode,
    PersistedCircuitBreakerSnapshot,
)
from daily_report_agent.storage.database import Database


NOW = datetime(
    2026,
    7,
    30,
    16,
    0,
    1,
    123456,
    tzinfo=timezone(timedelta(hours=8)),
)
KEY = CircuitBreakerKey("provider-a", "fetch_quote")


def _create(database: Database) -> PersistedCircuitBreakerSnapshot:
    with database.transaction() as connection:
        return CircuitBreakerRepository(connection).create_closed(KEY, now=NOW)


def _persist_open(database: Database) -> PersistedCircuitBreakerSnapshot:
    created = _create(database)
    transition = record_circuit_outcome(
        created.snapshot,
        CircuitBreakerPolicy(failure_threshold=1),
        outcome=CircuitOutcome.COUNTED_FAILURE,
        now=NOW + timedelta(seconds=1),
        error_code=RetryErrorCode.TIMEOUT,
    )
    with database.transaction() as connection:
        return CircuitBreakerRepository(connection).compare_and_swap(
            created,
            transition.after,
            updated_at=NOW + timedelta(seconds=1),
        )


def test_closed_round_trip_and_initial_version(storage_db: Database) -> None:
    created = _create(storage_db)

    with closing(storage_db.connect()) as connection:
        loaded = CircuitBreakerRepository(connection).load(KEY)

    assert loaded == created
    assert loaded is not None
    assert loaded.snapshot.state is CircuitBreakerState.CLOSED
    assert loaded.version == 0
    assert loaded.updated_at == NOW
    assert loaded.updated_at.tzinfo is timezone.utc


def test_open_round_trip_preserves_window_and_error_code(
    storage_db: Database,
) -> None:
    opened = _persist_open(storage_db)

    with closing(storage_db.connect()) as connection:
        loaded = CircuitBreakerRepository(connection).load(KEY)

    assert loaded == opened
    assert loaded is not None
    assert loaded.snapshot.state is CircuitBreakerState.OPEN
    assert loaded.snapshot.open_until == opened.snapshot.open_until
    assert loaded.snapshot.last_error_code is RetryErrorCode.TIMEOUT
    assert loaded.version == 1


def test_half_open_round_trip_preserves_active_probe(
    storage_db: Database,
) -> None:
    opened = _persist_open(storage_db)
    assert opened.snapshot.open_until is not None
    transition = evaluate_circuit(
        opened.snapshot,
        now=opened.snapshot.open_until,
    )
    with storage_db.transaction() as connection:
        stored = CircuitBreakerRepository(connection).compare_and_swap(
            opened,
            transition.after,
            updated_at=opened.snapshot.open_until,
        )

    with closing(storage_db.connect()) as connection:
        loaded = CircuitBreakerRepository(connection).load(KEY)

    assert loaded == stored
    assert loaded is not None
    assert loaded.snapshot.state is CircuitBreakerState.HALF_OPEN
    assert loaded.snapshot.half_open_probe_active is True
    assert loaded.version == 2


def test_load_missing_returns_none(storage_db: Database) -> None:
    with closing(storage_db.connect()) as connection:
        loaded = CircuitBreakerRepository(connection).load(KEY)

    assert loaded is None


def test_repeated_create_returns_existing_open_without_overwrite(
    storage_db: Database,
) -> None:
    opened = _persist_open(storage_db)

    with storage_db.transaction() as connection:
        repeated = CircuitBreakerRepository(connection).create_closed(
            KEY,
            now=NOW + timedelta(minutes=10),
        )

    assert repeated == opened


def test_persisted_wrapper_is_immutable(storage_db: Database) -> None:
    persisted = _create(storage_db)

    with pytest.raises(FrozenInstanceError):
        persisted.version = 3  # type: ignore[misc]
    with pytest.raises(ValueError, match="version 不得为负"):
        PersistedCircuitBreakerSnapshot(
            snapshot=persisted.snapshot,
            version=-1,
            updated_at=NOW,
        )


@pytest.mark.parametrize(
    ("assignment", "parameters"),
    [
        ("state = ?", ("broken",)),
        ("half_open_probe_active = ?", (2,)),
        ("consecutive_failures = ?", (-1,)),
        ("version = ?", (-1,)),
        ("last_transition_at = ?", ("not-a-time",)),
        ("updated_at = ?", ("2020-01-01T00:00:00.000000Z",)),
        (
            "state = ?, consecutive_failures = 1, opened_at = ?, "
            "open_until = NULL, last_failure_at = ?, last_error_code = ?",
            (
                "open",
                "2026-07-30T08:00:00.000000Z",
                "2026-07-30T08:00:00.000000Z",
                "timeout",
            ),
        ),
    ],
)
def test_corrupt_database_rows_are_safely_rejected(
    storage_db: Database,
    assignment: str,
    parameters: tuple[object, ...],
) -> None:
    _create(storage_db)
    with closing(storage_db.connect()) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE provider_circuit_breakers SET {assignment}",
            parameters,
        )
        connection.commit()

        with pytest.raises(CircuitStorageError) as raised:
            CircuitBreakerRepository(connection).load(KEY)

    assert raised.value.code is CircuitStorageErrorCode.STATE_CORRUPT
    assert "provider-a" not in str(raised.value)
    assert "not-a-time" not in str(raised.value)


def test_repository_sql_failure_has_fixed_safe_error() -> None:
    database = Database(":memory:")
    connection = database.connect()
    repository = CircuitBreakerRepository(connection)
    connection.close()

    with pytest.raises(CircuitStorageError) as raised:
        repository.load(KEY)

    assert raised.value.code is CircuitStorageErrorCode.SQL_FAILED
    message = str(raised.value)
    assert "SELECT" not in message
    assert "provider-a" not in message
    assert ":memory:" not in message


def test_compare_and_swap_increments_version(storage_db: Database) -> None:
    created = _create(storage_db)
    transition = record_circuit_outcome(
        created.snapshot,
        CircuitBreakerPolicy(),
        outcome=CircuitOutcome.COUNTED_FAILURE,
        now=NOW + timedelta(seconds=1),
        error_code=RetryErrorCode.TIMEOUT,
    )

    with storage_db.transaction() as connection:
        updated = CircuitBreakerRepository(connection).compare_and_swap(
            created,
            transition.after,
            updated_at=NOW + timedelta(seconds=1),
        )

    assert updated.version == 1
    assert updated.snapshot.consecutive_failures == 1


def test_stale_cas_fails_without_overwriting_new_state(
    storage_db: Database,
) -> None:
    stale = _create(storage_db)
    first_transition = record_circuit_outcome(
        stale.snapshot,
        CircuitBreakerPolicy(),
        outcome=CircuitOutcome.COUNTED_FAILURE,
        now=NOW + timedelta(seconds=1),
        error_code=RetryErrorCode.TIMEOUT,
    )
    with storage_db.transaction() as connection:
        winner = CircuitBreakerRepository(connection).compare_and_swap(
            stale,
            first_transition.after,
            updated_at=NOW + timedelta(seconds=1),
        )

    stale_transition = record_circuit_outcome(
        stale.snapshot,
        CircuitBreakerPolicy(),
        outcome=CircuitOutcome.COUNTED_FAILURE,
        now=NOW + timedelta(seconds=2),
        error_code=RetryErrorCode.NETWORK_ERROR,
    )
    with pytest.raises(CircuitStorageError) as raised:
        with storage_db.transaction() as connection:
            CircuitBreakerRepository(connection).compare_and_swap(
                stale,
                stale_transition.after,
                updated_at=NOW + timedelta(seconds=2),
            )

    assert raised.value.code is CircuitStorageErrorCode.STATE_CONFLICT
    with closing(storage_db.connect()) as connection:
        current = CircuitBreakerRepository(connection).load(KEY)
    assert current == winner


def test_two_connections_read_same_version_and_only_one_cas_succeeds(
    storage_db: Database,
) -> None:
    _create(storage_db)
    first_connection = storage_db.connect()
    second_connection = storage_db.connect()
    try:
        first = CircuitBreakerRepository(first_connection).load(KEY)
        second = CircuitBreakerRepository(second_connection).load(KEY)
    finally:
        first_connection.close()
        second_connection.close()
    assert first is not None and second is not None
    assert first.version == second.version == 0

    transition = record_circuit_outcome(
        first.snapshot,
        CircuitBreakerPolicy(),
        outcome=CircuitOutcome.COUNTED_FAILURE,
        now=NOW + timedelta(seconds=1),
        error_code=RetryErrorCode.TIMEOUT,
    )
    with storage_db.transaction() as connection:
        winner = CircuitBreakerRepository(connection).compare_and_swap(
            first,
            transition.after,
            updated_at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(CircuitStorageError) as raised:
        with storage_db.transaction() as connection:
            CircuitBreakerRepository(connection).compare_and_swap(
                second,
                transition.after,
                updated_at=NOW + timedelta(seconds=1),
            )

    assert winner.version == 1
    assert raised.value.code is CircuitStorageErrorCode.STATE_CONFLICT


def test_cas_missing_row_uses_fixed_not_found_error(
    storage_db: Database,
) -> None:
    persisted = _create(storage_db)
    with storage_db.transaction() as connection:
        connection.execute("DELETE FROM provider_circuit_breakers")

    with pytest.raises(CircuitStorageError) as raised:
        with storage_db.transaction() as connection:
            CircuitBreakerRepository(connection).compare_and_swap(
                persisted,
                persisted.snapshot,
                updated_at=NOW,
            )

    assert raised.value.code is CircuitStorageErrorCode.STATE_NOT_FOUND
