from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
)
from daily_report_agent.providers.errors import (
    ProviderAuthenticationError,
    ProviderBlockedError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from daily_report_agent.providers.routing import (
    DataRouteMode,
    ErrorClassification,
    FallbackDecision,
    ProviderErrorClass,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRouter,
    ProviderRuntimeStage,
    ResultEvaluation,
    RetryAttempt,
    RetryDecision,
    RetryErrorCode,
    RetryPolicy,
    RetryReasonCode,
    RetryableErrorCode,
    RouteErrorCode,
    RoutePolicy,
    RouteStatus,
    RouteTerminalStatus,
    classify_provider_error,
    decide_retry,
    require_route_mode_enabled,
)


ROOT = Path(__file__).parents[3]


def _registration(
    provider_id: str,
    *,
    enabled: bool = True,
    stage: ProviderRuntimeStage = ProviderRuntimeStage.SHADOW_ELIGIBLE,
) -> ProviderRegistration:
    descriptor = ProviderDescriptor(
        provider_id=provider_id,
        display_name=provider_id,
        capabilities=frozenset({ProviderCapability.QUOTE}),
        markets=frozenset({"cn"}),
    )
    return ProviderRegistration(
        descriptor=descriptor,
        capability=ProviderCapability.QUOTE,
        market="cn",
        priority=10,
        enabled=enabled,
        runtime_stage=stage,
    )


def _policy(
    candidates: tuple[str, ...],
    *,
    budget: int,
    fallback_on_empty: bool = False,
) -> RoutePolicy:
    return RoutePolicy(
        market="cn",
        capability=ProviderCapability.QUOTE,
        candidate_provider_ids=candidates,
        allow_legacy_fallback=False,
        max_call_budget=budget,
        mode=DataRouteMode.PROVIDER_SHADOW,
        fallback_on_empty=fallback_on_empty,
    )


def _result(
    registration: ProviderRegistration,
    *items: str,
) -> ProviderResult[str]:
    return ProviderResult(
        provider=registration.descriptor,
        items=tuple(items),
    )


def _provider_error(
    error_type: type[Exception],
    provider_id: str = "provider-a",
    *,
    safe_message: str = "safe synthetic failure",
):
    return error_type(
        provider_id=provider_id,
        operation="fake_route",
        safe_message=safe_message,
        code="upstream_failure",
    )


class SequenceInvoker:
    def __init__(self, outcomes: dict[str, tuple[object, ...]]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []
        self._indexes: dict[str, int] = {}

    def __call__(
        self,
        registration: ProviderRegistration,
    ) -> ProviderResult[str]:
        provider_id = registration.provider_id
        index = self._indexes.get(provider_id, 0)
        self._indexes[provider_id] = index + 1
        self.calls.append(provider_id)
        outcome = self.outcomes[provider_id][index]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]


def _evaluator(result: ProviderResult[str]) -> ResultEvaluation:
    if not result.items:
        return ResultEvaluation(RouteTerminalStatus.EMPTY)
    return ResultEvaluation(RouteTerminalStatus.SUCCESS)


def _router(
    registrations: tuple[ProviderRegistration, ...],
    policy: RoutePolicy,
    invoker: SequenceInvoker,
    *,
    retry_policy: RetryPolicy | None = None,
    evaluator=_evaluator,
) -> ProviderRouter[str]:
    return ProviderRouter(
        registry=ProviderRegistry(registrations),
        policy=policy,
        invoker=invoker,
        evaluator=evaluator,
        retry_policy=retry_policy,
    )


def test_default_retry_policy_is_conservative_and_m1_02_compatible() -> None:
    policy = RetryPolicy()
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (RuntimeError("not exposed"),),
            "provider-b": (_result(second, "ok"),),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=2),
        invoker,
    ).execute()

    assert policy.max_attempts_per_provider == 1
    assert policy.retry_on_rate_limit is False
    assert policy.retry_on_unknown is False
    assert invoker.calls == ["provider-a", "provider-b"]
    assert len(result.attempts) == 2
    assert len(result.attempts[0].retry_attempts) == 1
    assert result.final_provider_id == "provider-b"


def test_first_call_success_has_one_physical_attempt_and_stops() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {"provider-a": (_result(provider, "ok"),)}
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    assert invoker.calls == ["provider-a"]
    assert result.terminal_status is RouteTerminalStatus.SUCCESS
    assert result.call_budget_used == 1
    assert result.call_budget_remaining == 2
    assert result.call_budget_total == 3
    assert result.attempts[0].retry_attempts[0].retry_decision is (
        RetryDecision.SUCCESS
    )


@pytest.mark.parametrize(
    ("error_type", "error_code", "error_class"),
    [
        (
            ProviderTimeoutError,
            RetryErrorCode.TIMEOUT,
            ProviderErrorClass.TRANSIENT,
        ),
        (
            ProviderNetworkError,
            RetryErrorCode.NETWORK_ERROR,
            ProviderErrorClass.TRANSIENT,
        ),
        (
            ProviderUnavailableError,
            RetryErrorCode.PROVIDER_UNAVAILABLE,
            ProviderErrorClass.UNAVAILABLE,
        ),
    ],
)
def test_default_retryable_provider_error_retries_then_succeeds(
    error_type,
    error_code: RetryErrorCode,
    error_class: ProviderErrorClass,
) -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(error_type),
                _result(provider, "ok"),
            )
        }
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=2),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=2),
    ).execute()

    route_attempt = result.attempts[0]
    assert result.status is RouteStatus.SELECTED
    assert len(result.attempts) == 1
    assert len(route_attempt.retry_attempts) == 2
    assert route_attempt.retry_attempts[0].error_code is error_code
    assert route_attempt.retry_attempts[0].error_class is error_class
    assert route_attempt.retry_attempts[0].retry_decision is RetryDecision.RETRY
    assert route_attempt.retry_attempts[1].retry_decision is RetryDecision.SUCCESS


def test_multiple_retryable_errors_then_success_keep_one_route_attempt() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderTimeoutError),
                _provider_error(ProviderNetworkError),
                _result(provider, "ok"),
            )
        }
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    assert len(result.attempts) == 1
    assert len(result.attempts[0].retry_attempts) == 3
    assert [item.provider_attempt_index for item in result.attempts[0].retry_attempts] == [
        1,
        2,
        3,
    ]
    assert result.call_budget_used == 3


def test_retryable_error_code_set_can_conservatively_disable_timeout_retry() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (_provider_error(ProviderTimeoutError),),
            "provider-b": (_result(second, "ok"),),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=3),
        invoker,
        retry_policy=RetryPolicy(
            max_attempts_per_provider=3,
            retryable_error_codes=frozenset(
                {RetryableErrorCode.NETWORK_ERROR}
            ),
        ),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-b"]
    assert len(result.attempts[0].retry_attempts) == 1
    assert result.attempts[0].retry_attempts[0].retry_reason_code is (
        RetryReasonCode.NON_RETRYABLE_ERROR
    )


def test_retryable_error_stops_at_per_provider_maximum() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderTimeoutError),
                _provider_error(ProviderTimeoutError),
            )
        }
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=5),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=2),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-a"]
    assert result.status is RouteStatus.REJECTED
    assert result.call_budget_used == 2
    assert result.call_budget_remaining == 3
    assert result.attempts[0].retry_attempts[-1].retry_reason_code is (
        RetryReasonCode.MAX_ATTEMPTS_REACHED
    )


def test_retry_exhaustion_can_fallback_and_succeed() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderTimeoutError),
                _provider_error(ProviderTimeoutError),
            ),
            "provider-b": (_result(second, "ok"),),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=2),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-a", "provider-b"]
    assert result.final_provider_id == "provider-b"
    assert len(result.attempts) == 2
    assert result.attempts[0].fallback_triggered is True
    assert result.call_budget_used == 3
    assert result.call_budget_remaining == 0


def test_retry_exhaustion_without_fallback_returns_failed() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderUnavailableError),
                _provider_error(ProviderUnavailableError),
            )
        }
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=2),
    ).execute()

    assert result.status is RouteStatus.REJECTED
    assert result.terminal_status is RouteTerminalStatus.FAILED
    assert len(result.attempts[0].retry_attempts) == 2


def test_retry_exhaustion_with_no_budget_skips_fallback_provider() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderTimeoutError),
                _provider_error(ProviderTimeoutError),
            )
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=2),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=2),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-a"]
    assert result.error_code is RouteErrorCode.CALL_BUDGET_EXHAUSTED
    assert result.attempts[1].terminal_status is RouteTerminalStatus.SKIPPED
    assert result.attempts[1].error_code == "call_budget_exhausted"
    assert result.attempts[1].retry_attempts == ()


@pytest.mark.parametrize(
    ("budget", "expected_calls", "expected_retry_count"),
    [
        (0, 0, 0),
        (1, 1, 1),
        (2, 2, 2),
    ],
)
def test_global_budget_bounds_retry_calls(
    budget: int,
    expected_calls: int,
    expected_retry_count: int,
) -> None:
    provider = _registration("provider-a")
    outcomes = tuple(
        _provider_error(ProviderTimeoutError) for _ in range(3)
    )
    invoker = SequenceInvoker({"provider-a": outcomes})

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=budget),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    assert len(invoker.calls) == expected_calls
    assert result.call_budget_used == expected_calls
    assert result.call_budget_remaining == budget - expected_calls
    assert result.call_budget_total == budget
    if budget == 0:
        assert result.attempts[0].terminal_status is RouteTerminalStatus.SKIPPED
    else:
        assert len(result.attempts[0].retry_attempts) == expected_retry_count


def test_invoker_exception_always_consumes_physical_budget() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {"provider-a": (RuntimeError("secret"),)}
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=1),
        invoker,
    ).execute()

    assert result.call_budget_used == 1
    assert result.call_budget_remaining == 0
    assert len(result.attempts[0].retry_attempts) == 1


def test_disabled_and_stage_skipped_candidates_do_not_consume_budget() -> None:
    disabled = _registration("provider-a", enabled=False)
    offline = _registration(
        "provider-b",
        stage=ProviderRuntimeStage.OFFLINE_ONLY,
    )
    enabled = _registration("provider-c")
    invoker = SequenceInvoker(
        {"provider-c": (_result(enabled, "ok"),)}
    )

    result = _router(
        (disabled, offline, enabled),
        _policy(("provider-a", "provider-b", "provider-c"), budget=1),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    assert invoker.calls == ["provider-c"]
    assert result.call_budget_used == 1
    assert result.attempts[0].retry_attempts == ()
    assert result.attempts[1].retry_attempts == ()


@pytest.mark.parametrize(
    ("error_type", "expected_class"),
    [
        (ProviderValidationError, ProviderErrorClass.VALIDATION),
        (ProviderParseError, ProviderErrorClass.PROTOCOL),
        (ProviderAuthenticationError, ProviderErrorClass.AUTHENTICATION),
        (ProviderBlockedError, ProviderErrorClass.BLOCKED),
    ],
)
def test_non_retryable_provider_error_does_not_repeat_same_provider(
    error_type,
    expected_class: ProviderErrorClass,
) -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(error_type),
                RuntimeError("must-not-retry"),
            ),
            "provider-b": (_result(second, "ok"),),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-b"]
    first_retry = result.attempts[0].retry_attempts[0]
    assert first_retry.error_class is expected_class
    assert first_retry.retry_decision is RetryDecision.FALLBACK


def test_ordinary_exception_default_does_not_retry() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {"provider-a": (RuntimeError("timeout 502 token=secret"),)}
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    retry_attempt = result.attempts[0].retry_attempts[0]
    assert invoker.calls == ["provider-a"]
    assert retry_attempt.error_class is ProviderErrorClass.UNKNOWN
    assert retry_attempt.retry_reason_code is RetryReasonCode.UNKNOWN_RETRY_DISABLED


def test_rate_limit_default_does_not_retry_and_falls_back() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (_provider_error(ProviderRateLimitError),),
            "provider-b": (_result(second, "ok"),),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    retry_attempt = result.attempts[0].retry_attempts[0]
    assert invoker.calls == ["provider-a", "provider-b"]
    assert retry_attempt.retry_decision is RetryDecision.FALLBACK
    assert retry_attempt.retry_reason_code is (
        RetryReasonCode.RATE_LIMIT_RETRY_DISABLED
    )


def test_rate_limit_retries_only_when_policy_explicitly_enables_it() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderRateLimitError),
                _result(provider, "ok"),
            )
        }
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=2),
        invoker,
        retry_policy=RetryPolicy(
            max_attempts_per_provider=2,
            retry_on_rate_limit=True,
        ),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-a"]
    assert result.attempts[0].retry_attempts[0].retry_reason_code is (
        RetryReasonCode.RATE_LIMIT_RETRY_ENABLED
    )


def test_unknown_exception_retries_only_when_policy_explicitly_enables_it() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                RuntimeError("opaque"),
                _result(provider, "ok"),
            )
        }
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=2),
        invoker,
        retry_policy=RetryPolicy(
            max_attempts_per_provider=2,
            retry_on_unknown=True,
        ),
    ).execute()

    assert result.status is RouteStatus.SELECTED
    assert invoker.calls == ["provider-a", "provider-a"]
    assert result.attempts[0].retry_attempts[0].retry_reason_code is (
        RetryReasonCode.UNKNOWN_RETRY_ENABLED
    )


def test_empty_and_partial_are_never_transport_retried() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (_result(first),),
            "provider-b": (_result(second, "partial"),),
        }
    )

    def evaluator(result: ProviderResult[str]) -> ResultEvaluation:
        if result.provider.provider_id == "provider-a":
            return ResultEvaluation(RouteTerminalStatus.EMPTY)
        return ResultEvaluation(
            RouteTerminalStatus.PARTIAL,
            FallbackDecision.STOP,
        )

    result = _router(
        (first, second),
        _policy(
            ("provider-a", "provider-b"),
            budget=4,
            fallback_on_empty=True,
        ),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
        evaluator=evaluator,
    ).execute()

    assert invoker.calls == ["provider-a", "provider-b"]
    assert [len(item.retry_attempts) for item in result.attempts] == [1, 1]
    assert result.terminal_status is RouteTerminalStatus.PARTIAL


def test_evaluator_failure_does_not_retry_provider() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (_result(first, "bad"),),
            "provider-b": (_result(second, "ok"),),
        }
    )

    def evaluator(result: ProviderResult[str]) -> ResultEvaluation:
        if result.provider.provider_id == "provider-a":
            raise RuntimeError("token=secret")
        return ResultEvaluation(RouteTerminalStatus.SUCCESS)

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
        evaluator=evaluator,
    ).execute()

    assert invoker.calls == ["provider-a", "provider-b"]
    assert len(result.attempts[0].retry_attempts) == 1
    assert result.attempts[0].retry_attempts[0].error_code is (
        RetryErrorCode.EVALUATOR_FAILED
    )


def test_invalid_provider_result_does_not_retry() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker({"provider-a": ("not-a-result",)})

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=3),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    assert invoker.calls == ["provider-a"]
    assert result.attempts[0].retry_attempts[0].error_code is (
        RetryErrorCode.INVALID_PROVIDER_RESULT
    )


@pytest.mark.parametrize("signal", [KeyboardInterrupt(), SystemExit(4)])
def test_process_control_signals_propagate_after_budget_entry(
    signal: BaseException,
) -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker({"provider-a": (signal,)})
    router = _router(
        (provider,),
        _policy(("provider-a",), budget=2),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=2),
    )

    with pytest.raises(type(signal)):
        router.execute()

    assert invoker.calls == ["provider-a"]


def test_global_and_provider_retry_indexes_are_strictly_increasing() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderTimeoutError),
                _provider_error(ProviderTimeoutError),
            ),
            "provider-b": (
                _provider_error(
                    ProviderNetworkError,
                    provider_id="provider-b",
                ),
                _result(second, "ok"),
            ),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=4),
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=2),
    ).execute()

    assert [
        [item.provider_attempt_index for item in attempt.retry_attempts]
        for attempt in result.attempts
    ] == [[1, 2], [1, 2]]
    assert [
        item.global_call_index
        for attempt in result.attempts
        for item in attempt.retry_attempts
    ] == [1, 2, 3, 4]
    assert result.call_budget_used + result.call_budget_remaining == (
        result.call_budget_total
    )
    assert result.call_budget_remaining >= 0


def test_policy_can_stop_after_retry_without_fallback() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = SequenceInvoker(
        {
            "provider-a": (
                _provider_error(ProviderTimeoutError),
                _provider_error(ProviderTimeoutError),
            ),
            "provider-b": (_result(second, "must-not-run"),),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=3),
        invoker,
        retry_policy=RetryPolicy(
            max_attempts_per_provider=2,
            allow_fallback_after_retry=False,
        ),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-a"]
    assert len(result.attempts) == 1
    assert result.attempts[0].retry_attempts[-1].retry_reason_code is (
        RetryReasonCode.RETRY_FALLBACK_DISABLED
    )


def test_retry_policy_and_attempt_are_immutable() -> None:
    policy = RetryPolicy(max_attempts_per_provider=2)
    attempt = RetryAttempt(
        provider_id="provider-a",
        provider_attempt_index=1,
        global_call_index=1,
        terminal_status=RouteTerminalStatus.FAILED,
        error_code=RetryErrorCode.TIMEOUT,
        error_class=ProviderErrorClass.TRANSIENT,
        retry_decision=RetryDecision.RETRY,
        retry_reason_code=RetryReasonCode.RETRYABLE_ERROR,
    )

    with pytest.raises(FrozenInstanceError):
        policy.max_attempts_per_provider = 3  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        attempt.global_call_index = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_attempts_per_provider": 0},
        {"max_attempts_per_provider": 101},
        {"max_attempts_per_provider": True},
        {"max_attempts_per_provider": 1.5},
        {"retryable_error_codes": {"timeout"}},
        {"retryable_error_codes": frozenset({""})},
        {"retryable_error_codes": frozenset({"new_unknown_code"})},
        {"retry_on_rate_limit": "false"},
        {"retry_on_unknown": 0},
        {"allow_fallback_after_retry": 1},
    ],
)
def test_retry_policy_rejects_invalid_fields(overrides: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        RetryPolicy(**overrides)  # type: ignore[arg-type]


def test_retry_attempt_rejects_invalid_or_inconsistent_fields() -> None:
    with pytest.raises(ValueError):
        RetryAttempt(
            provider_id="provider-a",
            provider_attempt_index=0,
            global_call_index=1,
            terminal_status=RouteTerminalStatus.FAILED,
            error_code=RetryErrorCode.TIMEOUT,
            error_class=ProviderErrorClass.TRANSIENT,
            retry_decision=RetryDecision.RETRY,
            retry_reason_code=RetryReasonCode.RETRYABLE_ERROR,
        )
    with pytest.raises(ValueError):
        RetryAttempt(
            provider_id="provider-a",
            provider_attempt_index=1,
            global_call_index=1,
            terminal_status=RouteTerminalStatus.SUCCESS,
            error_code=RetryErrorCode.TIMEOUT,
            error_class=ProviderErrorClass.TRANSIENT,
            retry_decision=RetryDecision.SUCCESS,
            retry_reason_code=RetryReasonCode.RESULT_ACCEPTED,
        )


def test_error_classification_never_uses_exception_message_content() -> None:
    ordinary = RuntimeError(
        "timeout 502 https://secret.invalid Token=SECRET Cookie=PRIVATE"
    )

    classification = classify_provider_error(ordinary)

    assert classification == ErrorClassification(
        RetryErrorCode.INVOKER_FAILED,
        ProviderErrorClass.UNKNOWN,
    )


def test_sensitive_exception_fields_never_enter_retry_audit_or_result_repr() -> None:
    provider = _registration("provider-a")
    secret = (
        "https://secret.invalid Header Authorization Token=TOP_SECRET "
        "Cookie=PRIVATE response body"
    )
    invoker = SequenceInvoker(
        {"provider-a": (RuntimeError(secret), RuntimeError(secret))}
    )

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=2),
        invoker,
        retry_policy=RetryPolicy(
            max_attempts_per_provider=2,
            retry_on_unknown=True,
        ),
    ).execute()

    rendered = repr(result)
    for forbidden in (
        "secret.invalid",
        "TOP_SECRET",
        "PRIVATE",
        "Authorization",
        "response body",
    ):
        assert forbidden not in rendered
    assert not hasattr(result.attempts[0].retry_attempts[0], "__dict__")


def test_decide_retry_is_deterministic_and_budget_bounded() -> None:
    classification = ErrorClassification(
        RetryErrorCode.TIMEOUT,
        ProviderErrorClass.TRANSIENT,
    )
    policy = RetryPolicy(max_attempts_per_provider=2)

    assert decide_retry(
        classification,
        policy,
        provider_attempt_index=1,
        call_budget_remaining=1,
        fallback_available=True,
    ) == decide_retry(
        classification,
        policy,
        provider_attempt_index=1,
        call_budget_remaining=1,
        fallback_available=True,
    )
    assert decide_retry(
        classification,
        policy,
        provider_attempt_index=1,
        call_budget_remaining=0,
        fallback_available=True,
    ).reason_code is RetryReasonCode.CALL_BUDGET_EXHAUSTED


def test_legacy_mode_never_executes_retry_state_machine() -> None:
    provider = _registration("provider-a")
    invoker = SequenceInvoker(
        {"provider-a": (_result(provider, "must-not-run"),)}
    )
    policy = RoutePolicy(
        market="cn",
        capability=ProviderCapability.QUOTE,
        candidate_provider_ids=("provider-a",),
        allow_legacy_fallback=True,
        max_call_budget=3,
        mode=DataRouteMode.LEGACY,
    )

    result = _router(
        (provider,),
        policy,
        invoker,
        retry_policy=RetryPolicy(max_attempts_per_provider=3),
    ).execute()

    assert result.status is RouteStatus.LEGACY
    assert result.call_budget_used == 0
    assert result.call_budget_remaining == 3
    assert result.attempts == ()
    assert invoker.calls == []


@pytest.mark.parametrize(
    "mode",
    [DataRouteMode.PROVIDER_SHADOW, DataRouteMode.PROVIDER_PRIMARY],
)
def test_main_stage_gate_still_rejects_provider_modes(
    mode: DataRouteMode,
) -> None:
    with pytest.raises(Exception) as raised:
        require_route_mode_enabled(mode)

    assert getattr(raised.value, "code", None) is (
        RouteErrorCode.ROUTE_STAGE_NOT_ENABLED
    )


def test_import_and_retry_router_construction_do_not_load_online_transport() -> None:
    code = """
import sys
from daily_report_agent.providers.contracts import ProviderCapability, ProviderDescriptor
from daily_report_agent.providers.routing import (
    DataRouteMode, ProviderRegistration, ProviderRegistry, ProviderRouter,
    ProviderRuntimeStage, RetryPolicy, RoutePolicy,
)
online = 'daily_report_agent.providers.tencent.online_transport'
calls = []
descriptor = ProviderDescriptor(
    provider_id='offline-provider',
    display_name='Offline',
    capabilities=frozenset({ProviderCapability.QUOTE}),
    markets=frozenset({'cn'}),
)
registration = ProviderRegistration(
    descriptor=descriptor,
    capability=ProviderCapability.QUOTE,
    market='cn',
    priority=1,
    enabled=True,
    runtime_stage=ProviderRuntimeStage.SHADOW_ELIGIBLE,
)
ProviderRouter(
    registry=ProviderRegistry((registration,)),
    policy=RoutePolicy(
        market='cn',
        capability=ProviderCapability.QUOTE,
        candidate_provider_ids=('offline-provider',),
        allow_legacy_fallback=False,
        max_call_budget=2,
        mode=DataRouteMode.PROVIDER_SHADOW,
    ),
    invoker=lambda registration: calls.append(registration),
    evaluator=lambda result: calls.append(result),
    retry_policy=RetryPolicy(max_attempts_per_provider=2),
)
assert calls == []
assert online not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
