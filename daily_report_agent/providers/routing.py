"""Provider 路由的纯离线契约、Registry 与确定性选择逻辑。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Iterable, Protocol, TypeVar

from .contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
    normalize_provider_error_code,
    normalize_provider_id,
)
from .errors import ProviderError


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
    PROVIDER_DISABLED = "provider_disabled"
    RUNTIME_STAGE_INELIGIBLE = "runtime_stage_ineligible"
    PROVIDER_FAILED = "provider_failed"
    INVOKER_FAILED = "invoker_failed"
    INVALID_PROVIDER_RESULT = "invalid_provider_result"
    EVALUATOR_FAILED = "evaluator_failed"
    INVALID_EVALUATION = "invalid_evaluation"


class RouteTerminalStatus(str, Enum):
    """一次候选调用或完整 Router 执行的封闭终态。"""

    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class FallbackDecision(str, Enum):
    """ResultEvaluator 对 partial 结果的显式继续决策。"""

    STOP = "stop"
    CONTINUE = "continue"


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


@dataclass(frozen=True, slots=True)
class ResultEvaluation:
    """通用结果判定；不包含任何 Quote、News 或 Profile 业务规则。"""

    terminal_status: RouteTerminalStatus
    fallback_decision: FallbackDecision = FallbackDecision.STOP

    def __post_init__(self) -> None:
        if self.terminal_status not in {
            RouteTerminalStatus.SUCCESS,
            RouteTerminalStatus.EMPTY,
            RouteTerminalStatus.PARTIAL,
        }:
            raise ValueError("ResultEvaluation 只允许 success、empty 或 partial")
        if not isinstance(self.fallback_decision, FallbackDecision):
            raise TypeError("fallback_decision 必须是 FallbackDecision")
        if (
            self.terminal_status is not RouteTerminalStatus.PARTIAL
            and self.fallback_decision is not FallbackDecision.STOP
        ):
            raise ValueError("只有 partial 结果可以由 evaluator 请求 fallback")


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
    fallback_on_empty: bool = False

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
        if not isinstance(self.fallback_on_empty, bool):
            raise TypeError("fallback_on_empty 必须是布尔值")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "candidate_provider_ids", candidates)


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RouteAttempt:
    """一个候选在单次 Router 执行中的安全、不可变审计记录。"""

    provider_id: str
    attempt_index: int
    terminal_status: RouteTerminalStatus
    item_count: int
    issue_count: int
    error_code: str | None
    selected: bool
    fallback_triggered: bool

    def __post_init__(self) -> None:
        provider_id = normalize_provider_id(self.provider_id)
        if (
            not isinstance(self.attempt_index, int)
            or isinstance(self.attempt_index, bool)
            or self.attempt_index <= 0
        ):
            raise ValueError("attempt_index 必须是正整数")
        if not isinstance(self.terminal_status, RouteTerminalStatus):
            raise TypeError("terminal_status 必须是 RouteTerminalStatus")
        for name, value in (
            ("item_count", self.item_count),
            ("issue_count", self.issue_count),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{name} 必须是非负整数")
        if self.error_code is not None:
            error_code = normalize_provider_error_code(self.error_code)
        else:
            error_code = None
        if not isinstance(self.selected, bool):
            raise TypeError("selected 必须是布尔值")
        if not isinstance(self.fallback_triggered, bool):
            raise TypeError("fallback_triggered 必须是布尔值")
        if self.terminal_status is RouteTerminalStatus.SKIPPED and (
            self.item_count != 0 or self.issue_count != 0
        ):
            raise ValueError("skipped attempt 的 item/issue count 必须为 0")
        if self.terminal_status in {
            RouteTerminalStatus.FAILED,
            RouteTerminalStatus.SKIPPED,
        } and error_code is None:
            raise ValueError("failed/skipped attempt 必须包含安全错误码")
        if self.terminal_status not in {
            RouteTerminalStatus.FAILED,
            RouteTerminalStatus.SKIPPED,
        } and error_code is not None:
            raise ValueError("成功完成的 attempt 不得包含错误码")
        if self.selected and self.terminal_status in {
            RouteTerminalStatus.FAILED,
            RouteTerminalStatus.SKIPPED,
        }:
            raise ValueError("failed/skipped attempt 不得被选中")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "error_code", error_code)


class ProviderInvoker(Protocol[T]):
    """显式注入的同步调用边界；Router 不构造具体 Provider。"""

    def __call__(
        self, registration: ProviderRegistration
    ) -> ProviderResult[T]:
        ...


class ResultEvaluator(Protocol[T]):
    """能力专属的纯结果判定边界。"""

    def __call__(self, result: ProviderResult[T]) -> ResultEvaluation:
        ...


@dataclass(frozen=True, slots=True)
class RouteResult(Generic[T]):
    """选择或执行结果；M1-02 执行字段均有兼容默认值。"""

    selected_provider: ProviderRegistration | None
    attempted_provider_ids: tuple[str, ...]
    used_legacy_fallback: bool
    status: RouteStatus
    error_code: RouteErrorCode | None = None
    provider_result: ProviderResult[T] | None = None
    attempts: tuple[RouteAttempt, ...] = ()
    terminal_status: RouteTerminalStatus | None = None
    call_budget_used: int = 0
    call_budget_remaining: int = 0
    retained_results: tuple[ProviderResult[T], ...] = ()

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
        if not isinstance(self.attempts, tuple) or any(
            not isinstance(attempt, RouteAttempt) for attempt in self.attempts
        ):
            raise TypeError("attempts 必须是 RouteAttempt tuple")
        if self.terminal_status is not None and not isinstance(
            self.terminal_status, RouteTerminalStatus
        ):
            raise TypeError("terminal_status 必须是 RouteTerminalStatus 或 None")
        for name, value in (
            ("call_budget_used", self.call_budget_used),
            ("call_budget_remaining", self.call_budget_remaining),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"{name} 必须是非负整数")
        if not isinstance(self.retained_results, tuple) or any(
            not isinstance(result, ProviderResult)
            for result in self.retained_results
        ):
            raise TypeError("retained_results 必须是 ProviderResult tuple")
        if self.attempts:
            expected_indexes = tuple(range(1, len(self.attempts) + 1))
            if tuple(item.attempt_index for item in self.attempts) != expected_indexes:
                raise ValueError("RouteAttempt index 必须从 1 连续递增")
            if attempted != tuple(item.provider_id for item in self.attempts):
                raise ValueError("attempted_provider_ids 必须与 attempts 一致")
            actual_calls = sum(
                item.terminal_status is not RouteTerminalStatus.SKIPPED
                for item in self.attempts
            )
            if self.call_budget_used != actual_calls:
                raise ValueError("call_budget_used 必须等于非 skipped attempt 数")
        selected_attempts = tuple(item for item in self.attempts if item.selected)
        if len(selected_attempts) > 1:
            raise ValueError("最多只能有一个 selected RouteAttempt")
        if self.selected_provider is not None and self.attempts:
            if (
                len(selected_attempts) != 1
                or selected_attempts[0].provider_id
                != self.selected_provider.provider_id
            ):
                raise ValueError("selected_provider 必须与 selected attempt 一致")
        if (
            self.provider_result is not None
            and self.attempts
            and self.provider_result not in self.retained_results
        ):
            raise ValueError("最终 ProviderResult 必须保留在 retained_results")
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
        if self.terminal_status in {
            RouteTerminalStatus.SUCCESS,
            RouteTerminalStatus.EMPTY,
            RouteTerminalStatus.PARTIAL,
        } and self.provider_result is None:
            raise ValueError("可用终态必须包含 ProviderResult")
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


class ProviderRouter(Generic[T]):
    """同步串行执行显式候选的纯离线通用状态机。"""

    __slots__ = ("_registry", "_policy", "_invoker", "_evaluator")

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        policy: RoutePolicy,
        invoker: ProviderInvoker[T],
        evaluator: ResultEvaluator[T],
    ) -> None:
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("registry 必须是 ProviderRegistry")
        if not isinstance(policy, RoutePolicy):
            raise TypeError("policy 必须是 RoutePolicy")
        if not callable(invoker):
            raise TypeError("invoker 必须可调用")
        if not callable(evaluator):
            raise TypeError("evaluator 必须可调用")
        self._registry = registry
        self._policy = policy
        self._invoker = invoker
        self._evaluator = evaluator

    def execute(self) -> RouteResult[T]:
        """执行一次 route；每个候选最多同步调用一次且严格受总预算约束。"""
        policy = self._policy
        if policy.mode is DataRouteMode.LEGACY:
            return RouteResult(
                selected_provider=None,
                attempted_provider_ids=(),
                used_legacy_fallback=False,
                status=RouteStatus.LEGACY,
                terminal_status=RouteTerminalStatus.SKIPPED,
                call_budget_remaining=policy.max_call_budget,
            )

        attempts: list[RouteAttempt] = []
        retained_results: list[ProviderResult[T]] = []
        call_budget_used = 0
        final_error = RouteErrorCode.NO_ELIGIBLE_PROVIDER
        final_terminal = RouteTerminalStatus.SKIPPED
        required_stage = _STAGE_BY_MODE[policy.mode]
        candidate_count = len(policy.candidate_provider_ids)

        for position, provider_id in enumerate(policy.candidate_provider_ids):
            attempt_index = position + 1
            try:
                registration = self._registry.get(
                    provider_id,
                    capability=policy.capability,
                    market=policy.market,
                )
            except RouteContractError as exc:
                attempts.append(
                    _skipped_attempt(
                        provider_id,
                        attempt_index,
                        exc.code,
                    )
                )
                final_error = exc.code
                continue

            if not registration.enabled:
                attempts.append(
                    _skipped_attempt(
                        provider_id,
                        attempt_index,
                        RouteErrorCode.PROVIDER_DISABLED,
                    )
                )
                final_error = RouteErrorCode.PROVIDER_DISABLED
                continue
            if registration.runtime_stage is not required_stage:
                attempts.append(
                    _skipped_attempt(
                        provider_id,
                        attempt_index,
                        RouteErrorCode.RUNTIME_STAGE_INELIGIBLE,
                    )
                )
                final_error = RouteErrorCode.RUNTIME_STAGE_INELIGIBLE
                continue
            if call_budget_used >= policy.max_call_budget:
                attempts.append(
                    _skipped_attempt(
                        provider_id,
                        attempt_index,
                        RouteErrorCode.CALL_BUDGET_EXHAUSTED,
                    )
                )
                final_error = RouteErrorCode.CALL_BUDGET_EXHAUSTED
                continue

            # 在进入 Invoker 前先扣减，异常路径也不能绕过总调用预算。
            call_budget_used += 1
            can_fallback = (
                position + 1 < candidate_count
                and call_budget_used < policy.max_call_budget
            )
            try:
                provider_result = self._invoker(registration)
            except (KeyboardInterrupt, SystemExit):
                raise
            except ProviderError as exc:
                attempts.append(
                    RouteAttempt(
                        provider_id=provider_id,
                        attempt_index=attempt_index,
                        terminal_status=RouteTerminalStatus.FAILED,
                        item_count=0,
                        issue_count=0,
                        error_code=exc.code or RouteErrorCode.PROVIDER_FAILED.value,
                        selected=False,
                        fallback_triggered=can_fallback,
                    )
                )
                final_error = RouteErrorCode.PROVIDER_FAILED
                final_terminal = RouteTerminalStatus.FAILED
                continue
            except Exception:
                attempts.append(
                    RouteAttempt(
                        provider_id=provider_id,
                        attempt_index=attempt_index,
                        terminal_status=RouteTerminalStatus.FAILED,
                        item_count=0,
                        issue_count=0,
                        error_code=RouteErrorCode.INVOKER_FAILED.value,
                        selected=False,
                        fallback_triggered=can_fallback,
                    )
                )
                final_error = RouteErrorCode.INVOKER_FAILED
                final_terminal = RouteTerminalStatus.FAILED
                continue

            if (
                not isinstance(provider_result, ProviderResult)
                or provider_result.provider != registration.descriptor
            ):
                attempts.append(
                    RouteAttempt(
                        provider_id=provider_id,
                        attempt_index=attempt_index,
                        terminal_status=RouteTerminalStatus.FAILED,
                        item_count=0,
                        issue_count=0,
                        error_code=RouteErrorCode.INVALID_PROVIDER_RESULT.value,
                        selected=False,
                        fallback_triggered=can_fallback,
                    )
                )
                final_error = RouteErrorCode.INVALID_PROVIDER_RESULT
                final_terminal = RouteTerminalStatus.FAILED
                continue

            try:
                evaluation = self._evaluator(provider_result)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                attempts.append(
                    RouteAttempt(
                        provider_id=provider_id,
                        attempt_index=attempt_index,
                        terminal_status=RouteTerminalStatus.FAILED,
                        item_count=len(provider_result.items),
                        issue_count=len(provider_result.issues),
                        error_code=RouteErrorCode.EVALUATOR_FAILED.value,
                        selected=False,
                        fallback_triggered=can_fallback,
                    )
                )
                final_error = RouteErrorCode.EVALUATOR_FAILED
                final_terminal = RouteTerminalStatus.FAILED
                continue

            if not isinstance(evaluation, ResultEvaluation):
                attempts.append(
                    RouteAttempt(
                        provider_id=provider_id,
                        attempt_index=attempt_index,
                        terminal_status=RouteTerminalStatus.FAILED,
                        item_count=len(provider_result.items),
                        issue_count=len(provider_result.issues),
                        error_code=RouteErrorCode.INVALID_EVALUATION.value,
                        selected=False,
                        fallback_triggered=can_fallback,
                    )
                )
                final_error = RouteErrorCode.INVALID_EVALUATION
                final_terminal = RouteTerminalStatus.FAILED
                continue

            retained_results.append(provider_result)
            fallback_requested = (
                evaluation.terminal_status is RouteTerminalStatus.EMPTY
                and policy.fallback_on_empty
            ) or (
                evaluation.terminal_status is RouteTerminalStatus.PARTIAL
                and evaluation.fallback_decision is FallbackDecision.CONTINUE
            )
            should_fallback = fallback_requested and can_fallback
            selected = not should_fallback
            attempts.append(
                RouteAttempt(
                    provider_id=provider_id,
                    attempt_index=attempt_index,
                    terminal_status=evaluation.terminal_status,
                    item_count=len(provider_result.items),
                    issue_count=len(provider_result.issues),
                    error_code=None,
                    selected=selected,
                    fallback_triggered=should_fallback,
                )
            )
            if should_fallback:
                final_terminal = evaluation.terminal_status
                continue
            return RouteResult(
                selected_provider=registration,
                attempted_provider_ids=tuple(item.provider_id for item in attempts),
                used_legacy_fallback=False,
                status=RouteStatus.SELECTED,
                provider_result=provider_result,
                attempts=tuple(attempts),
                terminal_status=evaluation.terminal_status,
                call_budget_used=call_budget_used,
                call_budget_remaining=policy.max_call_budget - call_budget_used,
                retained_results=tuple(retained_results),
            )

        if final_terminal in {
            RouteTerminalStatus.SUCCESS,
            RouteTerminalStatus.EMPTY,
            RouteTerminalStatus.PARTIAL,
        }:
            # 先前可用结果请求了 fallback，但后续没有可选终态；结果仍在 retained_results。
            final_terminal = RouteTerminalStatus.FAILED
        return RouteResult(
            selected_provider=None,
            attempted_provider_ids=tuple(item.provider_id for item in attempts),
            used_legacy_fallback=policy.allow_legacy_fallback,
            status=(
                RouteStatus.LEGACY_FALLBACK
                if policy.allow_legacy_fallback
                else RouteStatus.REJECTED
            ),
            error_code=final_error,
            attempts=tuple(attempts),
            terminal_status=final_terminal,
            call_budget_used=call_budget_used,
            call_budget_remaining=policy.max_call_budget - call_budget_used,
            retained_results=tuple(retained_results),
        )


def _skipped_attempt(
    provider_id: str,
    attempt_index: int,
    error_code: RouteErrorCode,
) -> RouteAttempt:
    return RouteAttempt(
        provider_id=provider_id,
        attempt_index=attempt_index,
        terminal_status=RouteTerminalStatus.SKIPPED,
        item_count=0,
        issue_count=0,
        error_code=error_code.value,
        selected=False,
        fallback_triggered=False,
    )


def execute_route(
    *,
    registry: ProviderRegistry,
    policy: RoutePolicy,
    invoker: ProviderInvoker[T],
    evaluator: ResultEvaluator[T],
) -> RouteResult[T]:
    """一次性便捷入口；等价于构造 ProviderRouter 后调用 execute()。"""
    return ProviderRouter(
        registry=registry,
        policy=policy,
        invoker=invoker,
        evaluator=evaluator,
    ).execute()


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
