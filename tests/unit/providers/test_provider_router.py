from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
)
from daily_report_agent.providers.errors import ProviderUnavailableError
from daily_report_agent.providers.routing import (
    DataRouteMode,
    FallbackDecision,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRouter,
    ProviderRuntimeStage,
    ResultEvaluation,
    RouteAttempt,
    RouteErrorCode,
    RoutePolicy,
    RouteStatus,
    RouteTerminalStatus,
    execute_route,
)


ROOT = Path(__file__).parents[3]
ONLINE_MODULE = "daily_report_agent.providers.tencent.online_transport"


def _descriptor(provider_id: str) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=provider_id,
        display_name=provider_id,
        capabilities=frozenset({ProviderCapability.QUOTE}),
        markets=frozenset({"cn"}),
    )


def _registration(
    provider_id: str,
    *,
    priority: int = 10,
    enabled: bool = True,
    stage: ProviderRuntimeStage = ProviderRuntimeStage.SHADOW_ELIGIBLE,
) -> ProviderRegistration:
    return ProviderRegistration(
        descriptor=_descriptor(provider_id),
        capability=ProviderCapability.QUOTE,
        market="cn",
        priority=priority,
        enabled=enabled,
        runtime_stage=stage,
    )


def _policy(
    candidates: tuple[str, ...],
    *,
    budget: int = 3,
    mode: DataRouteMode = DataRouteMode.PROVIDER_SHADOW,
    fallback_on_empty: bool = False,
    legacy_fallback: bool = False,
) -> RoutePolicy:
    return RoutePolicy(
        market="cn",
        capability=ProviderCapability.QUOTE,
        candidate_provider_ids=candidates,
        allow_legacy_fallback=legacy_fallback,
        max_call_budget=budget,
        mode=mode,
        fallback_on_empty=fallback_on_empty,
    )


def _issue(provider_id: str) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.WARNING,
        category=IssueCategory.MISSING_DATA,
        provider=provider_id,
        operation="fake_route",
        message="synthetic issue",
        retryable=False,
        occurred_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        code="synthetic_issue",
    )


def _result(
    registration: ProviderRegistration,
    *items: str,
    issues: tuple[DataIssue, ...] = (),
) -> ProviderResult[str]:
    return ProviderResult(
        provider=registration.descriptor,
        items=tuple(items),
        issues=issues,
    )


class FakeInvoker:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def __call__(
        self,
        registration: ProviderRegistration,
    ) -> ProviderResult[str]:
        provider_id = registration.provider_id
        self.calls.append(provider_id)
        outcome = self.outcomes[provider_id]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, ProviderResult)
        return outcome


def _status_evaluator(
    statuses: dict[str, ResultEvaluation],
):
    def evaluate(result: ProviderResult[str]) -> ResultEvaluation:
        return statuses[result.provider.provider_id]

    return evaluate


def _router(
    registrations: tuple[ProviderRegistration, ...],
    policy: RoutePolicy,
    invoker: FakeInvoker,
    evaluator,
) -> ProviderRouter[str]:
    return ProviderRouter(
        registry=ProviderRegistry(registrations),
        policy=policy,
        invoker=invoker,
        evaluator=evaluator,
    )


def test_single_provider_success_records_selected_attempt_and_budget() -> None:
    provider = _registration("provider-a")
    invoker = FakeInvoker({"provider-a": _result(provider, "item")})

    result = _router(
        (provider,),
        _policy(("provider-a",), budget=2),
        invoker,
        _status_evaluator(
            {"provider-a": ResultEvaluation(RouteTerminalStatus.SUCCESS)}
        ),
    ).execute()

    assert result.status is RouteStatus.SELECTED
    assert result.terminal_status is RouteTerminalStatus.SUCCESS
    assert result.final_provider_id == "provider-a"
    assert result.call_budget_used == 1
    assert result.call_budget_remaining == 1
    assert result.attempts == (
        RouteAttempt(
            "provider-a",
            1,
            RouteTerminalStatus.SUCCESS,
            1,
            0,
            None,
            True,
            False,
        ),
    )


def test_success_stops_without_calling_later_candidate() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = FakeInvoker(
        {
            "provider-a": _result(first, "item"),
            "provider-b": _result(second, "must-not-run"),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b")),
        invoker,
        _status_evaluator(
            {
                "provider-a": ResultEvaluation(RouteTerminalStatus.SUCCESS),
                "provider-b": ResultEvaluation(RouteTerminalStatus.SUCCESS),
            }
        ),
    ).execute()

    assert invoker.calls == ["provider-a"]
    assert result.attempted_provider_ids == ("provider-a",)


def test_empty_stops_when_policy_does_not_request_fallback() -> None:
    provider = _registration("provider-a")
    invoker = FakeInvoker({"provider-a": _result(provider)})

    result = _router(
        (provider,),
        _policy(("provider-a",), fallback_on_empty=False),
        invoker,
        _status_evaluator(
            {"provider-a": ResultEvaluation(RouteTerminalStatus.EMPTY)}
        ),
    ).execute()

    assert result.terminal_status is RouteTerminalStatus.EMPTY
    assert result.provider_result == _result(provider)
    assert result.attempts[0].selected is True


def test_empty_policy_triggers_fallback_to_next_provider() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    first_result = _result(first)
    second_result = _result(second, "item")
    invoker = FakeInvoker(
        {"provider-a": first_result, "provider-b": second_result}
    )

    result = _router(
        (first, second),
        _policy(
            ("provider-a", "provider-b"),
            fallback_on_empty=True,
        ),
        invoker,
        _status_evaluator(
            {
                "provider-a": ResultEvaluation(RouteTerminalStatus.EMPTY),
                "provider-b": ResultEvaluation(RouteTerminalStatus.SUCCESS),
            }
        ),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-b"]
    assert result.final_provider_id == "provider-b"
    assert result.attempts[0].fallback_triggered is True
    assert result.retained_results == (first_result, second_result)


def test_partial_stops_and_preserves_result() -> None:
    provider = _registration("provider-a")
    partial_result = _result(
        provider,
        "partial-item",
        issues=(_issue(provider.provider_id),),
    )
    invoker = FakeInvoker({"provider-a": partial_result})

    result = _router(
        (provider,),
        _policy(("provider-a",)),
        invoker,
        _status_evaluator(
            {
                "provider-a": ResultEvaluation(
                    RouteTerminalStatus.PARTIAL,
                    FallbackDecision.STOP,
                )
            }
        ),
    ).execute()

    assert result.terminal_status is RouteTerminalStatus.PARTIAL
    assert result.provider_result is partial_result
    assert result.retained_results == (partial_result,)
    assert result.attempts[0].item_count == 1
    assert result.attempts[0].issue_count == 1


def test_partial_can_trigger_fallback_without_losing_partial_result() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    partial_result = _result(first, "partial")
    success_result = _result(second, "complete")
    invoker = FakeInvoker(
        {"provider-a": partial_result, "provider-b": success_result}
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b")),
        invoker,
        _status_evaluator(
            {
                "provider-a": ResultEvaluation(
                    RouteTerminalStatus.PARTIAL,
                    FallbackDecision.CONTINUE,
                ),
                "provider-b": ResultEvaluation(RouteTerminalStatus.SUCCESS),
            }
        ),
    ).execute()

    assert result.final_provider_id == "provider-b"
    assert result.retained_results == (partial_result, success_result)
    assert result.attempts[0].terminal_status is RouteTerminalStatus.PARTIAL
    assert result.attempts[0].selected is False
    assert result.attempts[0].fallback_triggered is True


def test_failed_invocation_triggers_serial_fallback() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = FakeInvoker(
        {
            "provider-a": RuntimeError("must not be exposed"),
            "provider-b": _result(second, "item"),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b")),
        invoker,
        _status_evaluator(
            {"provider-b": ResultEvaluation(RouteTerminalStatus.SUCCESS)}
        ),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-b"]
    assert result.final_provider_id == "provider-b"
    assert result.attempts[0].terminal_status is RouteTerminalStatus.FAILED
    assert result.attempts[0].error_code == "invoker_failed"
    assert result.attempts[0].fallback_triggered is True


def test_all_failed_returns_deterministic_rejected_result() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = FakeInvoker(
        {
            "provider-a": RuntimeError("first secret"),
            "provider-b": RuntimeError("second secret"),
        }
    )

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=2),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    ).execute()

    assert result.status is RouteStatus.REJECTED
    assert result.terminal_status is RouteTerminalStatus.FAILED
    assert result.error_code is RouteErrorCode.INVOKER_FAILED
    assert result.call_budget_used == 2
    assert result.call_budget_remaining == 0
    assert [item.terminal_status for item in result.attempts] == [
        RouteTerminalStatus.FAILED,
        RouteTerminalStatus.FAILED,
    ]


def test_all_failed_can_request_explicit_legacy_fallback_contract() -> None:
    provider = _registration("provider-a")
    invoker = FakeInvoker({"provider-a": RuntimeError("failed")})

    result = _router(
        (provider,),
        _policy(
            ("provider-a",),
            budget=1,
            legacy_fallback=True,
        ),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    ).execute()

    assert result.status is RouteStatus.LEGACY_FALLBACK
    assert result.used_legacy_fallback is True
    assert result.terminal_status is RouteTerminalStatus.FAILED
    assert result.provider_result is None


def test_candidate_exhaustion_without_candidates_is_skipped() -> None:
    invoker = FakeInvoker({})

    result = execute_route(
        registry=ProviderRegistry(),
        policy=_policy((), budget=3),
        invoker=invoker,
        evaluator=lambda result: pytest.fail("evaluator must not run"),
    )

    assert result.status is RouteStatus.REJECTED
    assert result.terminal_status is RouteTerminalStatus.SKIPPED
    assert result.error_code is RouteErrorCode.NO_ELIGIBLE_PROVIDER
    assert result.attempts == ()
    assert result.call_budget_remaining == 3


def test_zero_budget_skips_every_candidate_without_invocation() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = FakeInvoker({})

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=0),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    ).execute()

    assert invoker.calls == []
    assert result.call_budget_used == 0
    assert result.call_budget_remaining == 0
    assert [item.terminal_status for item in result.attempts] == [
        RouteTerminalStatus.SKIPPED,
        RouteTerminalStatus.SKIPPED,
    ]
    assert {item.error_code for item in result.attempts} == {
        "call_budget_exhausted"
    }


def test_budget_smaller_than_candidates_never_overcalls() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = FakeInvoker({"provider-a": RuntimeError("failed")})

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), budget=1),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    ).execute()

    assert invoker.calls == ["provider-a"]
    assert result.call_budget_used == 1
    assert result.attempts[1].terminal_status is RouteTerminalStatus.SKIPPED
    assert result.attempts[1].error_code == "call_budget_exhausted"


def test_skipped_disabled_provider_does_not_consume_budget() -> None:
    disabled = _registration("provider-a", enabled=False)
    enabled = _registration("provider-b")
    invoker = FakeInvoker({"provider-b": _result(enabled, "item")})

    result = _router(
        (disabled, enabled),
        _policy(("provider-a", "provider-b"), budget=1),
        invoker,
        _status_evaluator(
            {"provider-b": ResultEvaluation(RouteTerminalStatus.SUCCESS)}
        ),
    ).execute()

    assert invoker.calls == ["provider-b"]
    assert result.call_budget_used == 1
    assert result.attempts[0].terminal_status is RouteTerminalStatus.SKIPPED
    assert result.attempts[0].error_code == "provider_disabled"


def test_duplicate_candidate_is_rejected_before_router_execution() -> None:
    with pytest.raises(ValueError, match="重复"):
        _policy(("provider-a", "provider-a"))


def test_candidate_invocation_order_is_exactly_policy_order() -> None:
    first = _registration("provider-a", priority=100)
    second = _registration("provider-b", priority=1)
    invoker = FakeInvoker(
        {
            "provider-a": RuntimeError("failed"),
            "provider-b": _result(second, "item"),
        }
    )

    _router(
        (second, first),
        _policy(("provider-a", "provider-b")),
        invoker,
        _status_evaluator(
            {"provider-b": ResultEvaluation(RouteTerminalStatus.SUCCESS)}
        ),
    ).execute()

    assert invoker.calls == ["provider-a", "provider-b"]


def test_offline_only_is_skipped_in_production_without_budget_cost() -> None:
    offline = _registration(
        "provider-a",
        stage=ProviderRuntimeStage.OFFLINE_ONLY,
    )
    invoker = FakeInvoker({})

    result = _router(
        (offline,),
        _policy(
            ("provider-a",),
            mode=DataRouteMode.PROVIDER_PRIMARY,
            budget=1,
        ),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    ).execute()

    assert invoker.calls == []
    assert result.call_budget_used == 0
    assert result.attempts[0].error_code == "runtime_stage_ineligible"


@pytest.mark.parametrize(
    ("mode", "ineligible_stage", "eligible_stage"),
    [
        (
            DataRouteMode.PROVIDER_SHADOW,
            ProviderRuntimeStage.PRODUCTION_ELIGIBLE,
            ProviderRuntimeStage.SHADOW_ELIGIBLE,
        ),
        (
            DataRouteMode.PROVIDER_PRIMARY,
            ProviderRuntimeStage.SHADOW_ELIGIBLE,
            ProviderRuntimeStage.PRODUCTION_ELIGIBLE,
        ),
    ],
)
def test_shadow_and_production_stages_remain_isolated(
    mode: DataRouteMode,
    ineligible_stage: ProviderRuntimeStage,
    eligible_stage: ProviderRuntimeStage,
) -> None:
    first = _registration("provider-a", stage=ineligible_stage)
    second = _registration("provider-b", stage=eligible_stage)
    invoker = FakeInvoker({"provider-b": _result(second, "item")})

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b"), mode=mode, budget=1),
        invoker,
        _status_evaluator(
            {"provider-b": ResultEvaluation(RouteTerminalStatus.SUCCESS)}
        ),
    ).execute()

    assert invoker.calls == ["provider-b"]
    assert result.final_provider_id == "provider-b"
    assert result.attempts[0].terminal_status is RouteTerminalStatus.SKIPPED


def test_standard_provider_error_uses_only_safe_error_code() -> None:
    provider = _registration("provider-a")
    error = ProviderUnavailableError(
        provider_id="provider-a",
        operation="fake_route",
        safe_message="request unavailable",
        code="transport_unavailable",
    )
    invoker = FakeInvoker({"provider-a": error})

    result = _router(
        (provider,),
        _policy(("provider-a",)),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    ).execute()

    assert result.attempts[0].error_code == "transport_unavailable"
    assert result.error_code is RouteErrorCode.PROVIDER_FAILED
    assert "request unavailable" not in repr(result)


def test_ordinary_exception_body_is_not_retained_or_exposed() -> None:
    provider = _registration("provider-a")
    secret = "https://example.invalid?token=TOP_SECRET"
    invoker = FakeInvoker({"provider-a": RuntimeError(secret)})

    result = _router(
        (provider,),
        _policy(("provider-a",)),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    ).execute()

    assert result.attempts[0].error_code == "invoker_failed"
    assert secret not in repr(result)
    assert "TOP_SECRET" not in repr(result)


@pytest.mark.parametrize("signal", [KeyboardInterrupt(), SystemExit()])
def test_process_control_exceptions_propagate(signal: BaseException) -> None:
    provider = _registration("provider-a")
    invoker = FakeInvoker({"provider-a": signal})
    router = _router(
        (provider,),
        _policy(("provider-a",)),
        invoker,
        lambda result: pytest.fail("evaluator must not run"),
    )

    with pytest.raises(type(signal)):
        router.execute()


def test_evaluator_failure_is_safe_and_falls_back() -> None:
    first = _registration("provider-a")
    second = _registration("provider-b")
    invoker = FakeInvoker(
        {
            "provider-a": _result(first, "untrusted"),
            "provider-b": _result(second, "item"),
        }
    )

    def evaluator(result: ProviderResult[str]) -> ResultEvaluation:
        if result.provider.provider_id == "provider-a":
            raise RuntimeError("token=EVALUATOR_SECRET")
        return ResultEvaluation(RouteTerminalStatus.SUCCESS)

    result = _router(
        (first, second),
        _policy(("provider-a", "provider-b")),
        invoker,
        evaluator,
    ).execute()

    assert result.final_provider_id == "provider-b"
    assert result.attempts[0].error_code == "evaluator_failed"
    assert "EVALUATOR_SECRET" not in repr(result)
    assert result.retained_results == (_result(second, "item"),)


def test_invalid_evaluator_return_is_a_failed_attempt() -> None:
    provider = _registration("provider-a")
    invoker = FakeInvoker({"provider-a": _result(provider, "item")})

    result = _router(
        (provider,),
        _policy(("provider-a",)),
        invoker,
        lambda result: "success",
    ).execute()

    assert result.terminal_status is RouteTerminalStatus.FAILED
    assert result.attempts[0].error_code == "invalid_evaluation"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("item_count", -1),
        ("item_count", True),
        ("item_count", 1.5),
        ("issue_count", -1),
        ("issue_count", True),
        ("issue_count", "1"),
    ],
)
def test_route_attempt_rejects_invalid_counts(field: str, value: object) -> None:
    values = {
        "provider_id": "provider-a",
        "attempt_index": 1,
        "terminal_status": RouteTerminalStatus.SUCCESS,
        "item_count": 1,
        "issue_count": 0,
        "error_code": None,
        "selected": True,
        "fallback_triggered": False,
    }
    values[field] = value

    with pytest.raises(ValueError):
        RouteAttempt(**values)  # type: ignore[arg-type]


def test_route_attempt_and_executed_result_are_immutable() -> None:
    provider = _registration("provider-a")
    invoker = FakeInvoker({"provider-a": _result(provider, "item")})
    result = _router(
        (provider,),
        _policy(("provider-a",)),
        invoker,
        _status_evaluator(
            {"provider-a": ResultEvaluation(RouteTerminalStatus.SUCCESS)}
        ),
    ).execute()

    with pytest.raises(FrozenInstanceError):
        result.attempts[0].selected = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.call_budget_used = 99  # type: ignore[misc]


def test_legacy_router_execution_never_calls_injected_boundaries() -> None:
    invoker = FakeInvoker({})
    result = execute_route(
        registry=ProviderRegistry(),
        policy=_policy(
            ("provider-a",),
            mode=DataRouteMode.LEGACY,
            budget=2,
        ),
        invoker=invoker,
        evaluator=lambda result: pytest.fail("evaluator must not run"),
    )

    assert result.status is RouteStatus.LEGACY
    assert result.terminal_status is RouteTerminalStatus.SKIPPED
    assert result.call_budget_used == 0
    assert result.call_budget_remaining == 2
    assert invoker.calls == []


def test_import_and_router_construction_do_not_load_transport_or_invoke() -> None:
    code = """
import sys
from daily_report_agent.providers.contracts import ProviderCapability, ProviderDescriptor
from daily_report_agent.providers.routing import (
    DataRouteMode, ProviderRegistration, ProviderRegistry, ProviderRouter,
    ProviderRuntimeStage, RoutePolicy,
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
policy = RoutePolicy(
    market='cn',
    capability=ProviderCapability.QUOTE,
    candidate_provider_ids=('offline-provider',),
    allow_legacy_fallback=False,
    max_call_budget=1,
    mode=DataRouteMode.PROVIDER_SHADOW,
)
ProviderRouter(
    registry=ProviderRegistry((registration,)),
    policy=policy,
    invoker=lambda registration: calls.append(registration),
    evaluator=lambda result: calls.append(result),
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
