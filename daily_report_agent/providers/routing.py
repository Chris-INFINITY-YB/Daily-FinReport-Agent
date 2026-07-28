"""Provider 路由的纯离线契约、Registry 与确定性选择逻辑。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Iterable, TypeVar

from .contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
    normalize_provider_id,
)


_ALLOWED_MARKETS = frozenset({"cn", "us"})
_MAX_PRIORITY = 1_000_000
_MAX_CALL_BUDGET = 100


class DataRouteMode(str, Enum):
    """正式日报数据路由的封闭运行模式。"""

    LEGACY = "legacy"
    PROVIDER_SHADOW = "provider_shadow"
    PROVIDER_PRIMARY = "provider_primary"


class ProviderRuntimeStage(str, Enum):
    """Provider 当前获准参与的运行阶段。"""

    OFFLINE_ONLY = "offline_only"
    SHADOW_ELIGIBLE = "shadow_eligible"
    PRODUCTION_ELIGIBLE = "production_eligible"


class RouteStatus(str, Enum):
    """不包含 Provider 网络执行的路由选择终态。"""

    LEGACY = "legacy"
    SELECTED = "selected"
    LEGACY_FALLBACK = "legacy_fallback"
    REJECTED = "rejected"


class RouteErrorCode(str, Enum):
    """可安全记录和持久化的路由错误码。"""

    ROUTE_STAGE_NOT_ENABLED = "route_stage_not_enabled"
    DUPLICATE_REGISTRATION = "duplicate_registration"
    UNKNOWN_PROVIDER = "unknown_provider"
    CAPABILITY_MARKET_MISMATCH = "capability_market_mismatch"
    CALL_BUDGET_EXHAUSTED = "call_budget_exhausted"
    NO_ELIGIBLE_PROVIDER = "no_eligible_provider"


class RouteContractError(ValueError):
    """只公开固定错误码与安全消息的路由契约错误。"""

    def __init__(self, code: RouteErrorCode, safe_message: str) -> None:
        if not isinstance(code, RouteErrorCode):
            raise TypeError("code 必须是 RouteErrorCode")
        if not isinstance(safe_message, str) or not safe_message.strip():
            raise ValueError("safe_message 不能为空")
        self.code = code
        self.safe_message = safe_message.strip()
        super().__init__(self.safe_message)


def _normalize_market(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("market 必须是字符串")
    normalized = value.strip()
    if normalized not in _ALLOWED_MARKETS:
        raise ValueError("market 只允许 'cn' 或 'us'")
    return normalized


def _validate_priority(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("priority 必须是整数")
    if value < 0 or value > _MAX_PRIORITY:
        raise ValueError(f"priority 必须位于 0 到 {_MAX_PRIORITY} 之间")
    return value


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """Descriptor 在一个确定 capability/market 组合上的路由注册项。"""

    descriptor: ProviderDescriptor
    capability: ProviderCapability
    market: str
    priority: int
    enabled: bool
    runtime_stage: ProviderRuntimeStage

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, ProviderDescriptor):
            raise TypeError("descriptor 必须是 ProviderDescriptor")
        if not isinstance(self.capability, ProviderCapability):
            raise TypeError("capability 必须是 ProviderCapability")
        market = _normalize_market(self.market)
        priority = _validate_priority(self.priority)
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled 必须是布尔值")
        if not isinstance(self.runtime_stage, ProviderRuntimeStage):
            raise TypeError("runtime_stage 必须是 ProviderRuntimeStage")
        if self.capability not in self.descriptor.capabilities:
            raise RouteContractError(
                RouteErrorCode.CAPABILITY_MARKET_MISMATCH,
                "注册 capability 不在 ProviderDescriptor 声明范围内",
            )
        if market not in self.descriptor.markets:
            raise RouteContractError(
                RouteErrorCode.CAPABILITY_MARKET_MISMATCH,
                "注册 market 不在 ProviderDescriptor 声明范围内",
            )
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "priority", priority)

    @property
    def provider_id(self) -> str:
        return self.descriptor.provider_id

    @property
    def key(self) -> tuple[str, ProviderCapability, str]:
        return (self.provider_id, self.capability, self.market)


class ProviderRegistry:
    """只保存声明信息的进程内 Registry；构造、注册和查询均无 I/O。"""

    __slots__ = ("_registrations",)

    def __init__(
        self, registrations: Iterable[ProviderRegistration] = ()
    ) -> None:
        self._registrations: dict[
            tuple[str, ProviderCapability, str], ProviderRegistration
        ] = {}
        for registration in registrations:
            self.register(registration)

    def register(self, registration: ProviderRegistration) -> None:
        if not isinstance(registration, ProviderRegistration):
            raise TypeError("registration 必须是 ProviderRegistration")
        if registration.key in self._registrations:
            raise RouteContractError(
                RouteErrorCode.DUPLICATE_REGISTRATION,
                "Provider 注册项重复",
            )
        self._registrations[registration.key] = registration

    def get(
        self,
        provider_id: str,
        *,
        capability: ProviderCapability,
        market: str,
    ) -> ProviderRegistration:
        normalized_id = normalize_provider_id(provider_id)
        if not isinstance(capability, ProviderCapability):
            raise TypeError("capability 必须是 ProviderCapability")
        normalized_market = _normalize_market(market)
        key = (normalized_id, capability, normalized_market)
        registration = self._registrations.get(key)
        if registration is not None:
            return registration
        if any(item.provider_id == normalized_id for item in self._registrations.values()):
            raise RouteContractError(
                RouteErrorCode.CAPABILITY_MARKET_MISMATCH,
                "Provider 未注册请求的 capability/market 组合",
            )
        raise RouteContractError(
            RouteErrorCode.UNKNOWN_PROVIDER,
            "候选 Provider 未注册",
        )

    def query(
        self,
        *,
        capability: ProviderCapability,
        market: str,
        enabled_only: bool = True,
        runtime_stage: ProviderRuntimeStage | None = None,
    ) -> tuple[ProviderRegistration, ...]:
        if not isinstance(capability, ProviderCapability):
            raise TypeError("capability 必须是 ProviderCapability")
        normalized_market = _normalize_market(market)
        if not isinstance(enabled_only, bool):
            raise TypeError("enabled_only 必须是布尔值")
        if runtime_stage is not None and not isinstance(
            runtime_stage, ProviderRuntimeStage
        ):
            raise TypeError("runtime_stage 必须是 ProviderRuntimeStage 或 None")
        matches = (
            registration
            for registration in self._registrations.values()
            if registration.capability is capability
            and registration.market == normalized_market
            and (registration.enabled or not enabled_only)
            and (
                runtime_stage is None
                or registration.runtime_stage is runtime_stage
            )
        )
        return tuple(
            sorted(
                matches,
                key=lambda item: (item.priority, item.provider_id),
            )
        )

    def __len__(self) -> int:
        return len(self._registrations)


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    """一次 capability/market 路由的不可变选择策略。"""

    market: str
    capability: ProviderCapability
    candidate_provider_ids: tuple[str, ...]
    allow_legacy_fallback: bool
    max_call_budget: int
    mode: DataRouteMode

    def __post_init__(self) -> None:
        market = _normalize_market(self.market)
        if not isinstance(self.capability, ProviderCapability):
            raise TypeError("capability 必须是 ProviderCapability")
        if not isinstance(self.candidate_provider_ids, tuple):
            raise TypeError("candidate_provider_ids 必须是 tuple")
        candidates = tuple(
            normalize_provider_id(provider_id)
            for provider_id in self.candidate_provider_ids
        )
        if len(candidates) != len(set(candidates)):
            raise ValueError("candidate_provider_ids 不得包含重复项")
        if not isinstance(self.allow_legacy_fallback, bool):
            raise TypeError("allow_legacy_fallback 必须是布尔值")
        if (
            not isinstance(self.max_call_budget, int)
            or isinstance(self.max_call_budget, bool)
        ):
            raise TypeError("max_call_budget 必须是整数")
        if not 0 <= self.max_call_budget <= _MAX_CALL_BUDGET:
            raise ValueError(
                f"max_call_budget 必须位于 0 到 {_MAX_CALL_BUDGET} 之间"
            )
        if not isinstance(self.mode, DataRouteMode):
            raise TypeError("mode 必须是 DataRouteMode")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "candidate_provider_ids", candidates)


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RouteResult(Generic[T]):
    """路由选择结果；Provider 尚未执行时 provider_result 保持 None。"""

    selected_provider: ProviderRegistration | None
    attempted_provider_ids: tuple[str, ...]
    used_legacy_fallback: bool
    status: RouteStatus
    error_code: RouteErrorCode | None = None
    provider_result: ProviderResult[T] | None = None

    def __post_init__(self) -> None:
        if self.selected_provider is not None and not isinstance(
            self.selected_provider, ProviderRegistration
        ):
            raise TypeError("selected_provider 必须是 ProviderRegistration 或 None")
        if not isinstance(self.attempted_provider_ids, tuple):
            raise TypeError("attempted_provider_ids 必须是 tuple")
        attempted = tuple(
            normalize_provider_id(provider_id)
            for provider_id in self.attempted_provider_ids
        )
        if not isinstance(self.used_legacy_fallback, bool):
            raise TypeError("used_legacy_fallback 必须是布尔值")
        if not isinstance(self.status, RouteStatus):
            raise TypeError("status 必须是 RouteStatus")
        if self.error_code is not None and not isinstance(
            self.error_code, RouteErrorCode
        ):
            raise TypeError("error_code 必须是 RouteErrorCode 或 None")
        if self.provider_result is not None:
            if not isinstance(self.provider_result, ProviderResult):
                raise TypeError("provider_result 必须是 ProviderResult 或 None")
            if self.selected_provider is None:
                raise ValueError("ProviderResult 必须对应已选择的 Provider")
            if (
                self.provider_result.provider
                != self.selected_provider.descriptor
            ):
                raise ValueError("ProviderResult 与已选择 Provider 不一致")
        if self.status is RouteStatus.SELECTED and self.selected_provider is None:
            raise ValueError("selected 状态必须包含 selected_provider")
        if self.status is not RouteStatus.SELECTED and self.selected_provider is not None:
            raise ValueError("非 selected 状态不得包含 selected_provider")
        if (
            self.status is RouteStatus.LEGACY_FALLBACK
        ) != self.used_legacy_fallback:
            raise ValueError("used_legacy_fallback 与 status 不一致")
        if self.status is RouteStatus.REJECTED and self.error_code is None:
            raise ValueError("rejected 状态必须包含安全错误码")
        object.__setattr__(self, "attempted_provider_ids", attempted)

    @property
    def final_provider_id(self) -> str | None:
        if self.selected_provider is None:
            return None
        return self.selected_provider.provider_id


_STAGE_BY_MODE = {
    DataRouteMode.PROVIDER_SHADOW: ProviderRuntimeStage.SHADOW_ELIGIBLE,
    DataRouteMode.PROVIDER_PRIMARY: ProviderRuntimeStage.PRODUCTION_ELIGIBLE,
}


def select_route(
    registry: ProviderRegistry,
    policy: RoutePolicy,
) -> RouteResult[object]:
    """按显式候选顺序选择注册项，不构造 Provider，也不执行网络调用。"""
    if not isinstance(registry, ProviderRegistry):
        raise TypeError("registry 必须是 ProviderRegistry")
    if not isinstance(policy, RoutePolicy):
        raise TypeError("policy 必须是 RoutePolicy")
    if policy.mode is DataRouteMode.LEGACY:
        return RouteResult(
            selected_provider=None,
            attempted_provider_ids=(),
            used_legacy_fallback=False,
            status=RouteStatus.LEGACY,
        )

    if policy.max_call_budget == 0:
        return _unselected_result(
            policy,
            attempted=(),
            error_code=RouteErrorCode.CALL_BUDGET_EXHAUSTED,
        )

    required_stage = _STAGE_BY_MODE[policy.mode]
    attempted: list[str] = []
    for provider_id in policy.candidate_provider_ids:
        attempted.append(provider_id)
        try:
            registration = registry.get(
                provider_id,
                capability=policy.capability,
                market=policy.market,
            )
        except RouteContractError as exc:
            return _unselected_result(
                policy,
                attempted=tuple(attempted),
                error_code=exc.code,
            )
        if not registration.enabled:
            continue
        if registration.runtime_stage is not required_stage:
            continue
        return RouteResult(
            selected_provider=registration,
            attempted_provider_ids=tuple(attempted),
            used_legacy_fallback=False,
            status=RouteStatus.SELECTED,
        )
    return _unselected_result(
        policy,
        attempted=tuple(attempted),
        error_code=RouteErrorCode.NO_ELIGIBLE_PROVIDER,
    )


def _unselected_result(
    policy: RoutePolicy,
    *,
    attempted: tuple[str, ...],
    error_code: RouteErrorCode,
) -> RouteResult[object]:
    if policy.allow_legacy_fallback:
        return RouteResult(
            selected_provider=None,
            attempted_provider_ids=attempted,
            used_legacy_fallback=True,
            status=RouteStatus.LEGACY_FALLBACK,
            error_code=error_code,
        )
    return RouteResult(
        selected_provider=None,
        attempted_provider_ids=attempted,
        used_legacy_fallback=False,
        status=RouteStatus.REJECTED,
        error_code=error_code,
    )


def require_route_mode_enabled(mode: DataRouteMode) -> None:
    """M1-01 门禁：正式编排尚未就绪，非 legacy 模式必须在副作用前失败。"""
    if not isinstance(mode, DataRouteMode):
        raise TypeError("mode 必须是 DataRouteMode")
    if mode is not DataRouteMode.LEGACY:
        raise RouteContractError(
            RouteErrorCode.ROUTE_STAGE_NOT_ENABLED,
            "当前阶段尚未启用 provider_shadow/provider_primary 正式编排",
        )
