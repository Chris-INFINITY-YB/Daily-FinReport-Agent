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
from daily_report_agent.providers.routing import (
    DataRouteMode,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRuntimeStage,
    RouteContractError,
    RouteErrorCode,
    RoutePolicy,
    RouteResult,
    RouteStatus,
    select_route,
)


ROOT = Path(__file__).parents[3]


def _descriptor(
    provider_id: str,
    *,
    capabilities: frozenset[ProviderCapability] = frozenset(
        {ProviderCapability.QUOTE}
    ),
    markets: frozenset[str] = frozenset({"cn"}),
) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=provider_id,
        display_name=provider_id,
        capabilities=capabilities,
        markets=markets,
    )


def _registration(
    provider_id: str,
    *,
    priority: int = 10,
    enabled: bool = True,
    runtime_stage: ProviderRuntimeStage = ProviderRuntimeStage.SHADOW_ELIGIBLE,
    capability: ProviderCapability = ProviderCapability.QUOTE,
    market: str = "cn",
    descriptor: ProviderDescriptor | None = None,
) -> ProviderRegistration:
    return ProviderRegistration(
        descriptor=descriptor or _descriptor(provider_id),
        capability=capability,
        market=market,
        priority=priority,
        enabled=enabled,
        runtime_stage=runtime_stage,
    )


def _policy(
    *,
    candidates: tuple[str, ...] = ("provider-a",),
    fallback: bool = False,
    budget: int = 1,
    mode: DataRouteMode = DataRouteMode.PROVIDER_SHADOW,
) -> RoutePolicy:
    return RoutePolicy(
        market="cn",
        capability=ProviderCapability.QUOTE,
        candidate_provider_ids=candidates,
        allow_legacy_fallback=fallback,
        max_call_budget=budget,
        mode=mode,
    )


def test_registry_sorting_is_deterministic_by_priority_then_provider_id() -> None:
    registry = ProviderRegistry(
        (
            _registration("provider-z", priority=10),
            _registration("provider-b", priority=5),
            _registration("provider-a", priority=5),
        )
    )

    assert [
        item.provider_id
        for item in registry.query(
            capability=ProviderCapability.QUOTE,
            market="cn",
        )
    ] == ["provider-a", "provider-b", "provider-z"]


def test_duplicate_registration_is_safely_rejected() -> None:
    registration = _registration("provider-a")
    registry = ProviderRegistry((registration,))

    with pytest.raises(RouteContractError) as raised:
        registry.register(registration)

    assert raised.value.code is RouteErrorCode.DUPLICATE_REGISTRATION
    assert "provider-a" not in raised.value.safe_message


@pytest.mark.parametrize(
    ("capability", "market"),
    [
        (ProviderCapability.NEWS, "cn"),
        (ProviderCapability.QUOTE, "us"),
    ],
)
def test_registration_rejects_descriptor_capability_or_market_mismatch(
    capability: ProviderCapability,
    market: str,
) -> None:
    with pytest.raises(RouteContractError) as raised:
        _registration(
            "provider-a",
            capability=capability,
            market=market,
        )

    assert raised.value.code is RouteErrorCode.CAPABILITY_MARKET_MISMATCH


def test_registry_get_distinguishes_unknown_from_capability_market_mismatch() -> None:
    registry = ProviderRegistry((_registration("provider-a"),))

    with pytest.raises(RouteContractError) as mismatch:
        registry.get(
            "provider-a",
            capability=ProviderCapability.NEWS,
            market="cn",
        )
    with pytest.raises(RouteContractError) as unknown:
        registry.get(
            "provider-missing",
            capability=ProviderCapability.QUOTE,
            market="cn",
        )

    assert mismatch.value.code is RouteErrorCode.CAPABILITY_MARKET_MISMATCH
    assert unknown.value.code is RouteErrorCode.UNKNOWN_PROVIDER


def test_disabled_provider_is_not_selected() -> None:
    registry = ProviderRegistry(
        (
            _registration("provider-a", enabled=False),
            _registration("provider-b"),
        )
    )

    result = select_route(
        registry,
        _policy(candidates=("provider-a", "provider-b")),
    )

    assert result.status is RouteStatus.SELECTED
    assert result.final_provider_id == "provider-b"
    assert result.attempted_provider_ids == ("provider-a", "provider-b")


def test_offline_only_provider_never_enters_production_route() -> None:
    registry = ProviderRegistry(
        (
            _registration(
                "provider-a",
                runtime_stage=ProviderRuntimeStage.OFFLINE_ONLY,
            ),
        )
    )

    result = select_route(
        registry,
        _policy(mode=DataRouteMode.PROVIDER_PRIMARY),
    )

    assert result.status is RouteStatus.REJECTED
    assert result.error_code is RouteErrorCode.NO_ELIGIBLE_PROVIDER
    assert result.final_provider_id is None


def test_shadow_and_production_candidate_stages_are_isolated() -> None:
    registry = ProviderRegistry(
        (
            _registration(
                "shadow-provider",
                runtime_stage=ProviderRuntimeStage.SHADOW_ELIGIBLE,
            ),
            _registration(
                "production-provider",
                runtime_stage=ProviderRuntimeStage.PRODUCTION_ELIGIBLE,
            ),
        )
    )

    shadow = select_route(
        registry,
        _policy(candidates=("production-provider", "shadow-provider")),
    )
    production = select_route(
        registry,
        _policy(
            candidates=("shadow-provider", "production-provider"),
            mode=DataRouteMode.PROVIDER_PRIMARY,
        ),
    )

    assert shadow.final_provider_id == "shadow-provider"
    assert production.final_provider_id == "production-provider"


@pytest.mark.parametrize(
    ("fallback", "expected_status", "used_fallback"),
    [
        (False, RouteStatus.REJECTED, False),
        (True, RouteStatus.LEGACY_FALLBACK, True),
    ],
)
def test_legacy_fallback_is_explicit(
    fallback: bool,
    expected_status: RouteStatus,
    used_fallback: bool,
) -> None:
    registry = ProviderRegistry(
        (
            _registration(
                "provider-a",
                runtime_stage=ProviderRuntimeStage.OFFLINE_ONLY,
            ),
        )
    )

    result = select_route(
        registry,
        _policy(fallback=fallback),
    )

    assert result.status is expected_status
    assert result.used_legacy_fallback is used_fallback
    assert result.error_code is RouteErrorCode.NO_ELIGIBLE_PROVIDER


def test_zero_call_budget_never_selects_or_attempts_provider() -> None:
    registry = ProviderRegistry((_registration("provider-a"),))

    result = select_route(registry, _policy(budget=0))

    assert result.status is RouteStatus.REJECTED
    assert result.error_code is RouteErrorCode.CALL_BUDGET_EXHAUSTED
    assert result.attempted_provider_ids == ()


@pytest.mark.parametrize("budget", [-1, 101, True, 1.5])
def test_call_budget_rejects_invalid_boundaries(budget: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _policy(budget=budget)  # type: ignore[arg-type]


def test_legacy_mode_does_not_consult_registry_or_fallback() -> None:
    result = select_route(
        ProviderRegistry(),
        _policy(
            candidates=("not-registered",),
            fallback=True,
            budget=0,
            mode=DataRouteMode.LEGACY,
        ),
    )

    assert result == RouteResult(
        selected_provider=None,
        attempted_provider_ids=(),
        used_legacy_fallback=False,
        status=RouteStatus.LEGACY,
    )


def test_policy_registration_and_result_are_immutable() -> None:
    registration = _registration("provider-a")
    policy = _policy()
    result = select_route(ProviderRegistry((registration,)), policy)

    with pytest.raises(FrozenInstanceError):
        registration.priority = 20  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        policy.mode = DataRouteMode.LEGACY  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = RouteStatus.REJECTED  # type: ignore[misc]


def test_route_result_can_carry_only_matching_standard_provider_result() -> None:
    registration = _registration("provider-a")
    provider_result: ProviderResult[str] = ProviderResult(
        provider=registration.descriptor,
        items=("value",),
    )
    result: RouteResult[str] = RouteResult(
        selected_provider=registration,
        attempted_provider_ids=("provider-a",),
        used_legacy_fallback=False,
        status=RouteStatus.SELECTED,
        provider_result=provider_result,
    )

    assert result.provider_result is provider_result
    with pytest.raises(ValueError, match="不一致"):
        RouteResult(
            selected_provider=registration,
            attempted_provider_ids=("provider-a",),
            used_legacy_fallback=False,
            status=RouteStatus.SELECTED,
            provider_result=ProviderResult(provider=_descriptor("provider-b")),
        )


def test_import_and_registry_construction_do_not_load_online_transports() -> None:
    code = """
import sys
from daily_report_agent.providers.contracts import ProviderCapability, ProviderDescriptor
from daily_report_agent.providers.routing import (
    ProviderRegistration, ProviderRegistry, ProviderRuntimeStage,
)
blocked = {
    'daily_report_agent.providers.tencent.online_transport',
    'daily_report_agent.providers.cninfo.online_transport',
    'daily_report_agent.providers.eastmoney.online_transport',
}
assert blocked.isdisjoint(sys.modules)
descriptor = ProviderDescriptor(
    provider_id='offline-provider',
    display_name='Offline',
    capabilities=frozenset({ProviderCapability.QUOTE}),
    markets=frozenset({'cn'}),
)
ProviderRegistry((ProviderRegistration(
    descriptor=descriptor,
    capability=ProviderCapability.QUOTE,
    market='cn',
    priority=1,
    enabled=True,
    runtime_stage=ProviderRuntimeStage.OFFLINE_ONLY,
),))
assert blocked.isdisjoint(sys.modules)
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
