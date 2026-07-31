from __future__ import annotations

import inspect
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from daily_report_agent.providers.circuit_breaker import (
    CircuitBreakerKey,
    CircuitBreakerPolicy,
    CircuitBreakerState,
    CircuitDecision,
    CircuitOutcome,
    CircuitOutcomeOrigin,
)
from daily_report_agent.providers.routing import (
    ErrorClassification,
    ProviderErrorClass,
    RetryErrorCode,
)
from daily_report_agent.storage.circuit_breaker import (
    CircuitBreakerRepository,
    CircuitStorageError,
    CircuitStorageErrorCode,
    SQLiteCircuitBreakerStore,
)
from daily_report_agent.storage.database import Database


ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
KEY = CircuitBreakerKey("provider-a", "fetch_quote")


def _classification(
    error_class: ProviderErrorClass,
    error_code: RetryErrorCode,
) -> ErrorClassification:
    return ErrorClassification(error_code, error_class)


def _open(
    store: SQLiteCircuitBreakerStore,
    *,
    policy: CircuitBreakerPolicy | None = None,
):
    selected_policy = policy or CircuitBreakerPolicy(
        failure_threshold=1,
        open_duration=timedelta(seconds=30),
    )
    initial = store.preflight(KEY, selected_policy, now=NOW)
    return store.record_failure(
        KEY,
        selected_policy,
        _classification(
            ProviderErrorClass.TRANSIENT,
            RetryErrorCode.TIMEOUT,
        ),
        expected_version=initial.version,
        now=NOW + timedelta(seconds=1),
    )


def test_load_or_create_closed_is_idempotent(storage_db: Database) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)

    first = store.load_or_create_closed(KEY, now=NOW)
    second = store.load_or_create_closed(
        KEY,
        now=NOW + timedelta(minutes=1),
    )

    assert first == second
    assert first.version == 0
    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM provider_circuit_breakers"
        ).fetchone()[0]
    assert count == 1


def test_two_connections_initialize_same_key_as_one_row(
    storage_db: Database,
) -> None:
    barrier = Barrier(2)

    def initialize():
        barrier.wait()
        return SQLiteCircuitBreakerStore(storage_db).load_or_create_closed(
            KEY,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: initialize(), range(2)))

    assert results[0] == results[1]
    with closing(storage_db.connect()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM provider_circuit_breakers"
        ).fetchone()[0]
    assert count == 1


def test_closed_preflight_allows_without_version_change(
    storage_db: Database,
) -> None:
    result = SQLiteCircuitBreakerStore(storage_db).preflight(
        KEY,
        CircuitBreakerPolicy(),
        now=NOW,
    )

    assert result.transition.decision is CircuitDecision.ALLOW
    assert result.transition.changed is False
    assert result.version == 0


def test_open_window_preflight_skips_without_version_change(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    opened = _open(store)
    assert opened.transition.after.open_until is not None

    result = store.preflight(
        KEY,
        CircuitBreakerPolicy(),
        now=opened.transition.after.open_until - timedelta(microseconds=1),
    )

    assert result.transition.decision is CircuitDecision.SKIP
    assert result.transition.after.state is CircuitBreakerState.OPEN
    assert result.version == opened.version


def test_expired_open_preflight_persists_active_probe(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    opened = _open(store)
    assert opened.transition.after.open_until is not None

    result = store.preflight(
        KEY,
        CircuitBreakerPolicy(),
        now=opened.transition.after.open_until,
    )
    persisted = store.load(KEY)

    assert result.transition.decision is CircuitDecision.PROBE
    assert result.transition.after.state is CircuitBreakerState.HALF_OPEN
    assert result.transition.after.half_open_probe_active is True
    assert result.version == opened.version + 1
    assert persisted is not None
    assert persisted.snapshot == result.transition.after
    assert persisted.version == result.version


def test_active_half_open_preflight_skips_second_probe(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    opened = _open(store)
    assert opened.transition.after.open_until is not None
    first = store.preflight(
        KEY,
        CircuitBreakerPolicy(),
        now=opened.transition.after.open_until,
    )

    second = store.preflight(
        KEY,
        CircuitBreakerPolicy(),
        now=opened.transition.after.open_until,
    )

    assert first.transition.decision is CircuitDecision.PROBE
    assert second.transition.decision is CircuitDecision.SKIP
    assert second.version == first.version


def test_two_connections_compete_for_exactly_one_expired_probe(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    opened = _open(store)
    expires = opened.transition.after.open_until
    assert expires is not None
    barrier = Barrier(2)

    def preflight():
        barrier.wait()
        return SQLiteCircuitBreakerStore(storage_db).preflight(
            KEY,
            CircuitBreakerPolicy(),
            now=expires,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: preflight(), range(2)))

    decisions = [item.transition.decision for item in results]
    persisted = store.load(KEY)

    assert decisions.count(CircuitDecision.PROBE) == 1
    assert decisions.count(CircuitDecision.SKIP) == 1
    assert persisted is not None
    assert persisted.snapshot.state is CircuitBreakerState.HALF_OPEN
    assert persisted.snapshot.half_open_probe_active is True
    assert persisted.version == opened.version + 1


def test_counted_failure_is_persisted_and_matches_transition(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    initial = store.preflight(KEY, CircuitBreakerPolicy(), now=NOW)

    result = store.record_failure(
        KEY,
        CircuitBreakerPolicy(failure_threshold=2),
        _classification(
            ProviderErrorClass.TRANSIENT,
            RetryErrorCode.NETWORK_ERROR,
        ),
        expected_version=initial.version,
        now=NOW + timedelta(seconds=1),
    )
    persisted = store.load(KEY)

    assert result.transition.after.state is CircuitBreakerState.CLOSED
    assert result.transition.after.consecutive_failures == 1
    assert result.version == 1
    assert persisted is not None
    assert persisted.snapshot == result.transition.after


def test_threshold_and_immediate_failures_open(storage_db: Database) -> None:
    threshold_store = SQLiteCircuitBreakerStore(storage_db)
    threshold = _open(threshold_store)
    assert threshold.transition.after.state is CircuitBreakerState.OPEN

    second_key = CircuitBreakerKey("provider-b", "fetch_quote")
    initial = threshold_store.preflight(
        second_key,
        CircuitBreakerPolicy(),
        now=NOW,
    )
    immediate = threshold_store.record_failure(
        second_key,
        CircuitBreakerPolicy(failure_threshold=99),
        _classification(
            ProviderErrorClass.BLOCKED,
            RetryErrorCode.BLOCKED,
        ),
        expected_version=initial.version,
        now=NOW + timedelta(seconds=1),
    )

    assert immediate.transition.after.state is CircuitBreakerState.OPEN
    assert immediate.transition.after.last_error_code is RetryErrorCode.BLOCKED


@pytest.mark.parametrize(
    "origin",
    [
        CircuitOutcomeOrigin.EVALUATOR,
        CircuitOutcomeOrigin.MERGER,
        CircuitOutcomeOrigin.CALLER,
        CircuitOutcomeOrigin.INPUT,
    ],
)
def test_neutral_provider_and_non_provider_outcomes_do_not_count(
    storage_db: Database,
    origin: CircuitOutcomeOrigin,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    initial = store.preflight(KEY, CircuitBreakerPolicy(), now=NOW)

    validation = store.record_failure(
        KEY,
        CircuitBreakerPolicy(),
        _classification(
            ProviderErrorClass.VALIDATION,
            RetryErrorCode.VALIDATION,
        ),
        expected_version=initial.version,
        now=NOW + timedelta(seconds=1),
    )
    external = store.record_failure(
        KEY,
        CircuitBreakerPolicy(),
        _classification(
            ProviderErrorClass.UNKNOWN,
            RetryErrorCode.EVALUATOR_FAILED,
        ),
        expected_version=validation.version,
        now=NOW + timedelta(seconds=2),
        origin=origin,
    )

    assert validation.transition.changed is False
    assert external.transition.changed is False
    assert validation.version == external.version == 0
    persisted = store.load(KEY)
    assert persisted is not None
    assert persisted.snapshot.consecutive_failures == 0


def test_explicit_neutral_outcome_is_persisted_as_no_change(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    initial = store.preflight(KEY, CircuitBreakerPolicy(), now=NOW)

    result = store.record_outcome(
        KEY,
        CircuitBreakerPolicy(),
        CircuitOutcome.NEUTRAL,
        expected_version=initial.version,
        now=NOW + timedelta(seconds=1),
    )

    assert result.transition.changed is False
    assert result.version == initial.version
    assert store.load(KEY) is not None


def test_record_outcome_requires_existing_state(storage_db: Database) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)

    with pytest.raises(CircuitStorageError) as raised:
        store.record_outcome(
            KEY,
            CircuitBreakerPolicy(),
            CircuitOutcome.NEUTRAL,
            expected_version=0,
            now=NOW,
        )

    assert raised.value.code is CircuitStorageErrorCode.STATE_NOT_FOUND


def test_store_connection_failure_does_not_expose_database_path(
    tmp_path: Path,
) -> None:
    unsafe_path = tmp_path / "missing" / "token=secret.sqlite"
    store = SQLiteCircuitBreakerStore(Database(unsafe_path))

    with pytest.raises(CircuitStorageError) as raised:
        store.load(KEY)

    assert raised.value.code is CircuitStorageErrorCode.SQL_FAILED
    assert str(unsafe_path) not in str(raised.value)
    assert "token=secret" not in str(raised.value)


def test_closed_success_resets_failure_count(storage_db: Database) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    initial = store.preflight(KEY, CircuitBreakerPolicy(), now=NOW)
    failed = store.record_failure(
        KEY,
        CircuitBreakerPolicy(failure_threshold=2),
        _classification(
            ProviderErrorClass.TRANSIENT,
            RetryErrorCode.TIMEOUT,
        ),
        expected_version=initial.version,
        now=NOW + timedelta(seconds=1),
    )

    success = store.record_success(
        KEY,
        expected_version=failed.version,
        now=NOW + timedelta(seconds=2),
    )

    assert success.transition.after.state is CircuitBreakerState.CLOSED
    assert success.transition.after.consecutive_failures == 0
    assert success.transition.after.last_error_code is None


def test_half_open_success_closes_and_failure_reopens(
    storage_db: Database,
) -> None:
    success_key = CircuitBreakerKey("provider-success", "fetch_quote")
    failure_key = CircuitBreakerKey("provider-failure", "fetch_quote")
    policy = CircuitBreakerPolicy(
        failure_threshold=1,
        open_duration=timedelta(seconds=30),
    )
    store = SQLiteCircuitBreakerStore(storage_db)

    def claim(key: CircuitBreakerKey):
        initial = store.preflight(key, policy, now=NOW)
        opened = store.record_failure(
            key,
            policy,
            _classification(
                ProviderErrorClass.TRANSIENT,
                RetryErrorCode.TIMEOUT,
            ),
            expected_version=initial.version,
            now=NOW + timedelta(seconds=1),
        )
        assert opened.transition.after.open_until is not None
        return store.preflight(
            key,
            policy,
            now=opened.transition.after.open_until,
        )

    success_probe = claim(success_key)
    failure_probe = claim(failure_key)
    succeeded = store.record_success(
        success_key,
        expected_version=success_probe.version,
        now=success_probe.transition.after.last_transition_at
        + timedelta(seconds=1),
    )
    failed = store.record_failure(
        failure_key,
        policy,
        _classification(
            ProviderErrorClass.UNAVAILABLE,
            RetryErrorCode.PROVIDER_UNAVAILABLE,
        ),
        expected_version=failure_probe.version,
        now=failure_probe.transition.after.last_transition_at
        + timedelta(seconds=1),
    )

    assert succeeded.transition.after.state is CircuitBreakerState.CLOSED
    assert succeeded.transition.after.half_open_probe_active is False
    assert failed.transition.after.state is CircuitBreakerState.OPEN
    assert failed.transition.after.half_open_probe_active is False
    assert failed.transition.after.opened_at == failed.transition.after.last_failure_at


def test_stale_outcome_is_rejected_without_overwrite(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    initial = store.preflight(KEY, CircuitBreakerPolicy(), now=NOW)
    winner = store.record_failure(
        KEY,
        CircuitBreakerPolicy(),
        _classification(
            ProviderErrorClass.TRANSIENT,
            RetryErrorCode.TIMEOUT,
        ),
        expected_version=initial.version,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(CircuitStorageError) as raised:
        store.record_success(
            KEY,
            expected_version=initial.version,
            now=NOW + timedelta(seconds=2),
        )

    assert raised.value.code is CircuitStorageErrorCode.STATE_CONFLICT
    persisted = store.load(KEY)
    assert persisted is not None
    assert persisted.snapshot == winner.transition.after
    assert persisted.version == winner.version


def test_failed_state_write_rolls_back_entire_transaction(
    storage_db: Database,
    monkeypatch,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    initial = store.preflight(KEY, CircuitBreakerPolicy(), now=NOW)
    original = CircuitBreakerRepository.compare_and_swap

    def fail_after_update(self, expected, after, *, updated_at):
        original(self, expected, after, updated_at=updated_at)
        raise RuntimeError("force rollback")

    monkeypatch.setattr(
        CircuitBreakerRepository,
        "compare_and_swap",
        fail_after_update,
    )

    with pytest.raises(RuntimeError, match="force rollback"):
        store.record_failure(
            KEY,
            CircuitBreakerPolicy(),
            _classification(
                ProviderErrorClass.TRANSIENT,
                RetryErrorCode.TIMEOUT,
            ),
            expected_version=initial.version,
            now=NOW + timedelta(seconds=1),
        )

    persisted = store.load(KEY)
    assert persisted is not None
    assert persisted.version == initial.version
    assert persisted.snapshot == initial.transition.after


def test_time_rollback_is_rejected_and_state_unchanged(
    storage_db: Database,
) -> None:
    store = SQLiteCircuitBreakerStore(storage_db)
    initial = store.preflight(KEY, CircuitBreakerPolicy(), now=NOW)

    with pytest.raises(ValueError, match="不得早于"):
        store.record_success(
            KEY,
            expected_version=initial.version,
            now=NOW - timedelta(microseconds=1),
        )

    assert store.load(KEY) == store.load_or_create_closed(KEY, now=NOW)


def test_store_source_has_no_router_clock_sleep_or_network() -> None:
    import daily_report_agent.storage.circuit_breaker as module

    source = inspect.getsource(module)

    assert "ProviderRouter" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "sleep(" not in source
    assert "requests" not in source
    assert "socket" not in source


def test_importing_store_does_not_load_online_transports() -> None:
    code = """
import sys
from daily_report_agent.storage.circuit_breaker import SQLiteCircuitBreakerStore
online = {
    "daily_report_agent.providers.tencent.online_transport",
    "daily_report_agent.providers.cninfo.online_transport",
    "daily_report_agent.providers.eastmoney.online_transport",
}
assert online.isdisjoint(sys.modules), online.intersection(sys.modules)
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPYCACHEPREFIX"] = "/private/tmp/m1-04b-import-pycache"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
