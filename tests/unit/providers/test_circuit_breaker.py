from __future__ import annotations

import inspect
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from daily_report_agent.providers.circuit_breaker import (
    CircuitBreakerKey,
    CircuitBreakerPolicy,
    CircuitBreakerSnapshot,
    CircuitBreakerState,
    CircuitDecision,
    CircuitOutcome,
    CircuitOutcomeOrigin,
    CircuitTransition,
    CircuitTransitionReason,
    classify_circuit_outcome,
    create_closed_circuit,
    evaluate_circuit,
    record_circuit_failure,
    record_circuit_outcome,
    record_circuit_success,
)
from daily_report_agent.providers.routing import (
    DataRouteMode,
    ErrorClassification,
    ProviderErrorClass,
    ProviderRouter,
    RetryErrorCode,
    RouteContractError,
    require_route_mode_enabled,
)


ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)


def _key(
    provider_id: str = "provider-a",
    operation: str = "fetch_quote",
) -> CircuitBreakerKey:
    return CircuitBreakerKey(provider_id, operation)


def _closed(
    *,
    provider_id: str = "provider-a",
    operation: str = "fetch_quote",
) -> CircuitBreakerSnapshot:
    return create_closed_circuit(
        _key(provider_id, operation),
        now=NOW,
    )


def _classification(
    error_class: ProviderErrorClass,
    error_code: RetryErrorCode = RetryErrorCode.PROVIDER_FAILED,
) -> ErrorClassification:
    return ErrorClassification(
        error_code=error_code,
        error_class=error_class,
    )


def _open(
    *,
    policy: CircuitBreakerPolicy | None = None,
    error_class: ProviderErrorClass = ProviderErrorClass.BLOCKED,
) -> CircuitBreakerSnapshot:
    transition = record_circuit_failure(
        _closed(),
        policy or CircuitBreakerPolicy(),
        _classification(error_class, RetryErrorCode.BLOCKED),
        now=NOW + timedelta(seconds=1),
    )
    assert transition.after.state is CircuitBreakerState.OPEN
    return transition.after


def _half_open(
    *,
    policy: CircuitBreakerPolicy | None = None,
) -> CircuitBreakerSnapshot:
    snapshot = _open(policy=policy)
    assert snapshot.open_until is not None
    return evaluate_circuit(
        snapshot,
        now=snapshot.open_until,
    ).after


def test_key_trims_and_preserves_stable_safe_identifiers() -> None:
    key = CircuitBreakerKey(" provider-a ", " fetch_quote ")

    assert key == CircuitBreakerKey("provider-a", "fetch_quote")


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("provider_id", "", ValueError),
        ("provider_id", "Provider-A", ValueError),
        ("provider_id", "provider\nid", ValueError),
        ("provider_id", "https://example.invalid", ValueError),
        ("provider_id", "token=secret", ValueError),
        ("provider_id", 1, TypeError),
        ("operation", "", ValueError),
        ("operation", "fetch\nquote", ValueError),
        ("operation", "https://example.invalid", ValueError),
        ("operation", "Authorization: bearer", ValueError),
        ("operation", 1, TypeError),
    ],
)
def test_key_rejects_invalid_or_sensitive_shapes(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "provider_id": "provider-a",
        "operation": "fetch_quote",
    }
    values[field] = value

    with pytest.raises(error):
        CircuitBreakerKey(**values)  # type: ignore[arg-type]


def test_policy_defaults_are_closed_and_conservative() -> None:
    policy = CircuitBreakerPolicy()

    assert policy.failure_threshold == 3
    assert policy.open_duration == timedelta(minutes=5)
    assert policy.half_open_max_probes == 1
    assert ProviderErrorClass.VALIDATION not in policy.counted_error_classes
    assert policy.immediate_open_error_classes == frozenset(
        {
            ProviderErrorClass.BLOCKED,
            ProviderErrorClass.AUTHENTICATION,
            ProviderErrorClass.PROTOCOL,
        }
    )


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3"])
def test_policy_rejects_invalid_failure_threshold(value: object) -> None:
    error = TypeError if value is True or not isinstance(value, int) else ValueError
    with pytest.raises(error):
        CircuitBreakerPolicy(failure_threshold=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (timedelta(0), ValueError),
        (timedelta(seconds=-1), ValueError),
        (timedelta(days=366), ValueError),
        (300, TypeError),
        ("5m", TypeError),
    ],
)
def test_policy_rejects_invalid_open_duration(
    value: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        CircuitBreakerPolicy(open_duration=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 2, True, 1.0])
def test_policy_first_version_allows_exactly_one_probe(value: object) -> None:
    error = TypeError if value is True or not isinstance(value, int) else ValueError
    with pytest.raises(error):
        CircuitBreakerPolicy(half_open_max_probes=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"counted_error_classes": {ProviderErrorClass.TRANSIENT}},
        {"counted_error_classes": frozenset({"transient"})},
        {
            "counted_error_classes": frozenset(
                {ProviderErrorClass.VALIDATION}
            )
        },
        {
            "immediate_open_error_classes": frozenset(
                {ProviderErrorClass.TRANSIENT}
            )
        },
    ],
)
def test_policy_rejects_invalid_error_class_sets(
    overrides: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        CircuitBreakerPolicy(**overrides)  # type: ignore[arg-type]


def test_closed_factory_builds_valid_explicit_snapshot() -> None:
    snapshot = _closed()

    assert snapshot.state is CircuitBreakerState.CLOSED
    assert snapshot.consecutive_failures == 0
    assert snapshot.last_transition_at is NOW
    assert snapshot.opened_at is None
    assert snapshot.open_until is None
    assert snapshot.half_open_probe_active is False


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("state", "closed", TypeError),
        ("consecutive_failures", -1, ValueError),
        ("consecutive_failures", True, TypeError),
        ("last_transition_at", datetime(2026, 7, 28), ValueError),
        ("last_transition_at", "2026-07-28", TypeError),
        ("half_open_probe_active", 1, TypeError),
        ("last_error_code", "timeout", TypeError),
    ],
)
def test_snapshot_rejects_invalid_field_types_or_values(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    values = {
        "key": _key(),
        "state": CircuitBreakerState.CLOSED,
        "consecutive_failures": 0,
        "opened_at": None,
        "open_until": None,
        "last_transition_at": NOW,
        "last_failure_at": None,
        "last_success_at": None,
        "last_error_code": None,
        "half_open_probe_active": False,
    }
    values[field] = value

    with pytest.raises(error):
        CircuitBreakerSnapshot(**values)  # type: ignore[arg-type]


def test_open_snapshot_requires_complete_ordered_window() -> None:
    base = _closed()

    with pytest.raises(ValueError):
        replace(base, state=CircuitBreakerState.OPEN)
    with pytest.raises(ValueError):
        replace(
            base,
            state=CircuitBreakerState.OPEN,
            opened_at=NOW,
            open_until=NOW,
        )


def test_closed_snapshot_rejects_open_window_and_active_probe() -> None:
    base = _closed()

    with pytest.raises(ValueError):
        replace(base, opened_at=NOW, open_until=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError):
        replace(base, half_open_probe_active=True)


def test_snapshot_rejects_internal_time_reversal() -> None:
    base = _closed()

    with pytest.raises(ValueError):
        replace(base, consecutive_failures=1)
    with pytest.raises(ValueError):
        replace(base, last_error_code=RetryErrorCode.TIMEOUT)
    with pytest.raises(ValueError):
        replace(base, last_failure_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError):
        replace(base, last_success_at=NOW + timedelta(seconds=1))
    opened = _open()
    with pytest.raises(ValueError):
        replace(opened, state=CircuitBreakerState.HALF_OPEN)


@pytest.mark.parametrize(
    ("instance", "field"),
    [
        (_key(), "provider_id"),
        (CircuitBreakerPolicy(), "failure_threshold"),
        (_closed(), "state"),
        (
            CircuitTransition(
                before=_closed(),
                after=_closed(),
                decision=CircuitDecision.ALLOW,
                reason=CircuitTransitionReason.CLOSED_ALLOW,
                changed=False,
            ),
            "decision",
        ),
    ],
)
def test_core_models_are_immutable(instance: object, field: str) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(instance, field, "changed")


def test_transition_changed_must_match_snapshots() -> None:
    snapshot = _closed()

    with pytest.raises(ValueError):
        CircuitTransition(
            before=snapshot,
            after=snapshot,
            decision=CircuitDecision.ALLOW,
            reason=CircuitTransitionReason.CLOSED_ALLOW,
            changed=True,
        )


def test_closed_preflight_allows_without_mutation() -> None:
    snapshot = _closed()

    transition = evaluate_circuit(snapshot, now=NOW)

    assert transition.decision is CircuitDecision.ALLOW
    assert transition.reason is CircuitTransitionReason.CLOSED_ALLOW
    assert transition.after is snapshot
    assert transition.changed is False


def test_closed_success_resets_failure_state_and_clears_error() -> None:
    policy = CircuitBreakerPolicy(failure_threshold=3)
    failed = record_circuit_failure(
        _closed(),
        policy,
        _classification(
            ProviderErrorClass.TRANSIENT,
            RetryErrorCode.TIMEOUT,
        ),
        now=NOW + timedelta(seconds=1),
    ).after

    transition = record_circuit_success(
        failed,
        now=NOW + timedelta(seconds=2),
    )

    assert transition.after.state is CircuitBreakerState.CLOSED
    assert transition.after.consecutive_failures == 0
    assert transition.after.last_success_at == NOW + timedelta(seconds=2)
    assert transition.after.last_error_code is None
    assert transition.reason is CircuitTransitionReason.SUCCESS_RESET


def test_counted_failure_below_threshold_stays_closed() -> None:
    transition = record_circuit_failure(
        _closed(),
        CircuitBreakerPolicy(failure_threshold=2),
        _classification(
            ProviderErrorClass.UNAVAILABLE,
            RetryErrorCode.PROVIDER_UNAVAILABLE,
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert transition.after.state is CircuitBreakerState.CLOSED
    assert transition.after.consecutive_failures == 1
    assert transition.after.last_failure_at == NOW + timedelta(seconds=1)
    assert (
        transition.reason is CircuitTransitionReason.FAILURE_RECORDED
    )


def test_counted_failure_at_threshold_opens_with_explicit_window() -> None:
    policy = CircuitBreakerPolicy(
        failure_threshold=1,
        open_duration=timedelta(seconds=30),
    )
    at = NOW + timedelta(seconds=1)

    transition = record_circuit_failure(
        _closed(),
        policy,
        _classification(
            ProviderErrorClass.RATE_LIMITED,
            RetryErrorCode.RATE_LIMITED,
        ),
        now=at,
    )

    assert transition.after.state is CircuitBreakerState.OPEN
    assert transition.after.opened_at == at
    assert transition.after.open_until == at + timedelta(seconds=30)
    assert transition.decision is CircuitDecision.SKIP
    assert (
        transition.reason
        is CircuitTransitionReason.FAILURE_THRESHOLD_REACHED
    )


@pytest.mark.parametrize(
    ("error_class", "error_code"),
    [
        (ProviderErrorClass.BLOCKED, RetryErrorCode.BLOCKED),
        (
            ProviderErrorClass.AUTHENTICATION,
            RetryErrorCode.AUTHENTICATION,
        ),
        (ProviderErrorClass.PROTOCOL, RetryErrorCode.PROTOCOL),
    ],
)
def test_immediate_error_opens_on_first_failure(
    error_class: ProviderErrorClass,
    error_code: RetryErrorCode,
) -> None:
    transition = record_circuit_failure(
        _closed(),
        CircuitBreakerPolicy(failure_threshold=99),
        _classification(error_class, error_code),
        now=NOW + timedelta(seconds=1),
    )

    assert transition.after.state is CircuitBreakerState.OPEN
    assert transition.after.last_error_code is error_code
    assert transition.reason is CircuitTransitionReason.IMMEDIATE_OPEN


def test_validation_is_neutral_and_does_not_fake_success() -> None:
    snapshot = _closed()

    transition = record_circuit_failure(
        snapshot,
        CircuitBreakerPolicy(),
        _classification(
            ProviderErrorClass.VALIDATION,
            RetryErrorCode.VALIDATION,
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert transition.after is snapshot
    assert transition.reason is CircuitTransitionReason.NEUTRAL_OUTCOME
    assert transition.after.consecutive_failures == 0
    assert transition.after.last_success_at is None


def test_provider_and_operation_keys_isolate_snapshots() -> None:
    first = _closed(provider_id="provider-a", operation="fetch_quote")
    second = _closed(provider_id="provider-b", operation="fetch_quote")
    third = _closed(provider_id="provider-a", operation="fetch_profile")

    changed = record_circuit_failure(
        first,
        CircuitBreakerPolicy(),
        _classification(ProviderErrorClass.TRANSIENT),
        now=NOW + timedelta(seconds=1),
    ).after

    assert changed.key != second.key
    assert changed.key != third.key
    assert second.consecutive_failures == third.consecutive_failures == 0


def test_open_preflight_inside_window_skips_without_mutation() -> None:
    snapshot = _open()
    assert snapshot.open_until is not None

    transition = evaluate_circuit(
        snapshot,
        now=snapshot.open_until - timedelta(microseconds=1),
    )

    assert transition.decision is CircuitDecision.SKIP
    assert transition.reason is CircuitTransitionReason.OPEN_WINDOW_ACTIVE
    assert transition.after is snapshot
    assert transition.changed is False


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=10)])
def test_open_expiry_enters_half_open_and_claims_probe(
    offset: timedelta,
) -> None:
    snapshot = _open()
    assert snapshot.open_until is not None

    transition = evaluate_circuit(
        snapshot,
        now=snapshot.open_until + offset,
    )

    assert transition.after.state is CircuitBreakerState.HALF_OPEN
    assert transition.after.half_open_probe_active is True
    assert transition.decision is CircuitDecision.PROBE
    assert transition.reason is CircuitTransitionReason.OPEN_WINDOW_ELAPSED


def test_evaluate_rejects_time_rollback_and_naive_time() -> None:
    snapshot = _closed()

    with pytest.raises(ValueError):
        evaluate_circuit(snapshot, now=NOW - timedelta(microseconds=1))
    with pytest.raises(ValueError):
        evaluate_circuit(snapshot, now=datetime(2026, 7, 28))


def test_active_half_open_probe_blocks_second_probe_without_mutation() -> None:
    snapshot = _half_open()

    transition = evaluate_circuit(
        snapshot,
        now=snapshot.last_transition_at,
    )

    assert transition.decision is CircuitDecision.SKIP
    assert transition.reason is CircuitTransitionReason.HALF_OPEN_PROBE_BUSY
    assert transition.after is snapshot


def test_inactive_half_open_claims_exactly_one_probe() -> None:
    active = _half_open()
    inactive = replace(active, half_open_probe_active=False)

    transition = evaluate_circuit(
        inactive,
        now=inactive.last_transition_at + timedelta(seconds=1),
    )

    assert transition.decision is CircuitDecision.PROBE
    assert transition.after.half_open_probe_active is True
    assert (
        transition.reason
        is CircuitTransitionReason.HALF_OPEN_PROBE_ALLOWED
    )


def test_half_open_probe_success_closes_and_resets() -> None:
    snapshot = _half_open()
    at = snapshot.last_transition_at + timedelta(seconds=1)

    transition = record_circuit_success(snapshot, now=at)

    assert transition.after.state is CircuitBreakerState.CLOSED
    assert transition.after.consecutive_failures == 0
    assert transition.after.opened_at is None
    assert transition.after.open_until is None
    assert transition.after.last_error_code is None
    assert transition.after.half_open_probe_active is False
    assert transition.after.last_success_at == at
    assert (
        transition.reason
        is CircuitTransitionReason.HALF_OPEN_PROBE_SUCCEEDED
    )


@pytest.mark.parametrize(
    ("error_class", "error_code"),
    [
        (ProviderErrorClass.TRANSIENT, RetryErrorCode.TIMEOUT),
        (ProviderErrorClass.BLOCKED, RetryErrorCode.BLOCKED),
    ],
)
def test_half_open_probe_failure_reopens_with_fresh_window(
    error_class: ProviderErrorClass,
    error_code: RetryErrorCode,
) -> None:
    policy = CircuitBreakerPolicy(open_duration=timedelta(seconds=45))
    snapshot = _half_open(policy=policy)
    at = snapshot.last_transition_at + timedelta(seconds=3)

    transition = record_circuit_failure(
        snapshot,
        policy,
        _classification(error_class, error_code),
        now=at,
    )

    assert transition.after.state is CircuitBreakerState.OPEN
    assert transition.after.opened_at == at
    assert transition.after.open_until == at + timedelta(seconds=45)
    assert transition.after.last_failure_at == at
    assert transition.after.last_error_code is error_code
    assert transition.after.half_open_probe_active is False
    assert transition.reason is CircuitTransitionReason.HALF_OPEN_PROBE_FAILED


def test_half_open_neutral_releases_probe_without_changing_health() -> None:
    snapshot = _half_open()
    at = snapshot.last_transition_at + timedelta(seconds=1)

    transition = record_circuit_failure(
        snapshot,
        CircuitBreakerPolicy(),
        _classification(
            ProviderErrorClass.VALIDATION,
            RetryErrorCode.VALIDATION,
        ),
        now=at,
    )

    assert transition.after.state is CircuitBreakerState.HALF_OPEN
    assert transition.after.consecutive_failures == snapshot.consecutive_failures
    assert transition.after.half_open_probe_active is False
    assert transition.after.last_success_at == snapshot.last_success_at
    assert transition.reason is CircuitTransitionReason.NEUTRAL_OUTCOME


@pytest.mark.parametrize(
    "error_class",
    [
        ProviderErrorClass.TRANSIENT,
        ProviderErrorClass.UNAVAILABLE,
        ProviderErrorClass.RATE_LIMITED,
        ProviderErrorClass.PERMANENT,
        ProviderErrorClass.UNKNOWN,
    ],
)
def test_default_error_mapping_counts_provider_failures(
    error_class: ProviderErrorClass,
) -> None:
    assert (
        classify_circuit_outcome(
            _classification(error_class),
            CircuitBreakerPolicy(),
        )
        is CircuitOutcome.COUNTED_FAILURE
    )


@pytest.mark.parametrize(
    "error_class",
    [
        ProviderErrorClass.BLOCKED,
        ProviderErrorClass.AUTHENTICATION,
        ProviderErrorClass.PROTOCOL,
    ],
)
def test_default_error_mapping_immediately_opens(
    error_class: ProviderErrorClass,
) -> None:
    assert (
        classify_circuit_outcome(
            _classification(error_class),
            CircuitBreakerPolicy(),
        )
        is CircuitOutcome.IMMEDIATE_OPEN
    )


def test_default_error_mapping_treats_validation_as_neutral() -> None:
    assert (
        classify_circuit_outcome(
            _classification(ProviderErrorClass.VALIDATION),
            CircuitBreakerPolicy(),
        )
        is CircuitOutcome.NEUTRAL
    )


@pytest.mark.parametrize(
    "origin",
    [
        CircuitOutcomeOrigin.EVALUATOR,
        CircuitOutcomeOrigin.MERGER,
        CircuitOutcomeOrigin.CALLER,
        CircuitOutcomeOrigin.INPUT,
    ],
)
def test_non_provider_failures_are_neutral(
    origin: CircuitOutcomeOrigin,
) -> None:
    assert (
        classify_circuit_outcome(
            _classification(ProviderErrorClass.UNKNOWN),
            CircuitBreakerPolicy(),
            origin=origin,
        )
        is CircuitOutcome.NEUTRAL
    )


def test_result_recording_rejects_unsafe_or_inconsistent_inputs() -> None:
    snapshot = _closed()
    policy = CircuitBreakerPolicy()

    with pytest.raises(ValueError):
        record_circuit_outcome(
            snapshot,
            policy,
            CircuitOutcome.COUNTED_FAILURE,
            now=NOW,
        )
    with pytest.raises(ValueError):
        record_circuit_outcome(
            snapshot,
            policy,
            CircuitOutcome.SUCCESS,
            now=NOW,
            error_code=RetryErrorCode.TIMEOUT,
        )
    with pytest.raises(TypeError):
        record_circuit_outcome(
            snapshot,
            policy,
            CircuitOutcome.NEUTRAL,
            now=NOW,
            error_code="secret",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        record_circuit_outcome(
            snapshot,
            policy,
            CircuitOutcome.NEUTRAL,
            now=NOW,
            error_code=RetryErrorCode.VALIDATION,
        )


def test_open_and_unclaimed_half_open_reject_unmatched_results() -> None:
    opened = _open()
    half_open = _half_open()
    unclaimed = replace(half_open, half_open_probe_active=False)

    with pytest.raises(ValueError):
        record_circuit_success(opened, now=opened.last_transition_at)
    with pytest.raises(ValueError):
        record_circuit_success(
            unclaimed,
            now=unclaimed.last_transition_at,
        )
    with pytest.raises(ValueError):
        record_circuit_outcome(
            unclaimed,
            CircuitBreakerPolicy(),
            CircuitOutcome.COUNTED_FAILURE,
            now=unclaimed.last_transition_at,
            error_code=RetryErrorCode.TIMEOUT,
        )


def test_state_machine_is_deterministic_and_does_not_mutate_input() -> None:
    snapshot = _closed()
    policy = CircuitBreakerPolicy()
    classification = _classification(ProviderErrorClass.TRANSIENT)

    first = record_circuit_failure(
        snapshot,
        policy,
        classification,
        now=NOW + timedelta(seconds=1),
    )
    second = record_circuit_failure(
        snapshot,
        policy,
        classification,
        now=NOW + timedelta(seconds=1),
    )

    assert first == second
    assert snapshot == _closed()


def test_transition_never_contains_exception_body_or_request_secrets() -> None:
    sensitive = (
        "timeout https://example.invalid Authorization=Bearer-secret "
        "Cookie=session-secret"
    )
    transition = record_circuit_failure(
        _closed(),
        CircuitBreakerPolicy(),
        _classification(
            ProviderErrorClass.TRANSIENT,
            RetryErrorCode.TIMEOUT,
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert sensitive not in repr(transition)
    assert "example.invalid" not in repr(transition)
    assert "Bearer-secret" not in repr(transition)
    assert transition.reason.value == "failure_recorded"


def test_circuit_module_has_no_implicit_clock_sleep_or_network_primitives() -> None:
    import daily_report_agent.providers.circuit_breaker as circuit_breaker

    source = inspect.getsource(circuit_breaker)

    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "sleep(" not in source
    assert "requests" not in source
    assert "urllib" not in source
    assert "socket" not in source


def test_circuit_contract_is_not_integrated_into_provider_router() -> None:
    parameters = inspect.signature(ProviderRouter.__init__).parameters

    assert "circuit_breaker" not in parameters
    assert "circuit_repository" not in parameters


def test_legacy_remains_only_executable_route_mode() -> None:
    assert require_route_mode_enabled(DataRouteMode.LEGACY) is None

    for mode in (
        DataRouteMode.PROVIDER_SHADOW,
        DataRouteMode.PROVIDER_PRIMARY,
    ):
        with pytest.raises(RouteContractError):
            require_route_mode_enabled(mode)


def test_importing_circuit_contract_does_not_load_online_transports() -> None:
    code = """
import sys
from daily_report_agent.providers.circuit_breaker import (
    CircuitBreakerKey,
    CircuitBreakerPolicy,
    create_closed_circuit,
)
from datetime import datetime, timezone
create_closed_circuit(
    CircuitBreakerKey("provider-a", "fetch_quote"),
    now=datetime(2026, 7, 28, tzinfo=timezone.utc),
)
CircuitBreakerPolicy()
online = {
    "daily_report_agent.providers.tencent.online_transport",
    "daily_report_agent.providers.cninfo.online_transport",
    "daily_report_agent.providers.eastmoney.online_transport",
}
assert online.isdisjoint(sys.modules), online.intersection(sys.modules)
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = "/private/tmp/m1-04a-import-pycache"

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
