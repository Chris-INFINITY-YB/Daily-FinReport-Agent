"""Provider 路由的纯离线契约、Registry 与确定性选择逻辑。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Iterable, Protocol, TypeVar

from .contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
    normalize_provider_error_code,
    normalize_provider_id,
)
from .errors import (
    ProviderAuthenticationError,
    ProviderBlockedError,
    ProviderError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
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


class ProviderErrorClass(str, Enum):
    """只由异常类型确定的封闭 Provider 失败分类。"""

    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    PROTOCOL = "protocol"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class RetryErrorCode(str, Enum):
    """Retry 审计使用的封闭、稳定错误码。"""

    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    PROTOCOL = "protocol"
    PROVIDER_FAILED = "provider_failed"
    INVOKER_FAILED = "invoker_failed"
    INVALID_PROVIDER_RESULT = "invalid_provider_result"
    EVALUATOR_FAILED = "evaluator_failed"
    INVALID_EVALUATION = "invalid_evaluation"


class RetryDecision(str, Enum):
    """一次物理调用完成后的封闭状态机决策。"""

    RETRY = "retry"
    FALLBACK = "fallback"
    STOP = "stop"
    SUCCESS = "success"


class RetryReasonCode(str, Enum):
    """RetryDecision 的固定、无自由文本原因码。"""

    RESULT_ACCEPTED = "result_accepted"
    RESULT_FALLBACK_REQUESTED = "result_fallback_requested"
    RETRYABLE_ERROR = "retryable_error"
    RATE_LIMIT_RETRY_ENABLED = "rate_limit_retry_enabled"
    UNKNOWN_RETRY_ENABLED = "unknown_retry_enabled"
    NON_RETRYABLE_ERROR = "non_retryable_error"
    RATE_LIMIT_RETRY_DISABLED = "rate_limit_retry_disabled"
    UNKNOWN_RETRY_DISABLED = "unknown_retry_disabled"
    MAX_ATTEMPTS_REACHED = "max_attempts_reached"
    CALL_BUDGET_EXHAUSTED = "call_budget_exhausted"
    RETRY_FALLBACK_DISABLED = "retry_fallback_disabled"
    NO_FALLBACK_AVAILABLE = "no_fallback_available"


class RetryableErrorCode(str, Enum):
    """允许由 RetryPolicy.retryable_error_codes 配置的安全错误集合。"""

    TIMEOUT = RetryErrorCode.TIMEOUT.value
    NETWORK_ERROR = RetryErrorCode.NETWORK_ERROR.value
    PROVIDER_UNAVAILABLE = RetryErrorCode.PROVIDER_UNAVAILABLE.value


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


@dataclass(frozen=True, slots=True)
class ErrorClassification:
    """不读取异常正文的纯错误分类结果。"""

    error_code: RetryErrorCode
    error_class: ProviderErrorClass

    def __post_init__(self) -> None:
        if not isinstance(self.error_code, RetryErrorCode):
            raise TypeError("error_code 必须是 RetryErrorCode")
        if not isinstance(self.error_class, ProviderErrorClass):
            raise TypeError("error_class 必须是 ProviderErrorClass")


_DEFAULT_RETRYABLE_ERROR_CODES = frozenset(
    {
        RetryableErrorCode.TIMEOUT,
        RetryableErrorCode.NETWORK_ERROR,
        RetryableErrorCode.PROVIDER_UNAVAILABLE,
    }
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """单个 Provider 的纯离线重试策略；次数包含首次物理调用。"""

    max_attempts_per_provider: int = 1
    retryable_error_codes: frozenset[RetryableErrorCode] = (
        _DEFAULT_RETRYABLE_ERROR_CODES
    )
    retry_on_rate_limit: bool = False
    retry_on_unknown: bool = False
    allow_fallback_after_retry: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_attempts_per_provider, int)
            or isinstance(self.max_attempts_per_provider, bool)
        ):
            raise TypeError("max_attempts_per_provider 必须是整数")
        if not 1 <= self.max_attempts_per_provider <= _MAX_CALL_BUDGET:
            raise ValueError(
                f"max_attempts_per_provider 必须位于 1 到 {_MAX_CALL_BUDGET} 之间"
            )
        if not isinstance(self.retryable_error_codes, frozenset):
            raise TypeError("retryable_error_codes 必须是 frozenset")
        if any(
            not isinstance(code, RetryableErrorCode)
            for code in self.retryable_error_codes
        ):
            raise TypeError(
                "retryable_error_codes 只能包含 RetryableErrorCode"
            )
        for name, value in (
            ("retry_on_rate_limit", self.retry_on_rate_limit),
            ("retry_on_unknown", self.retry_on_unknown),
            ("allow_fallback_after_retry", self.allow_fallback_after_retry),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"{name} 必须是布尔值")


@dataclass(frozen=True, slots=True)
class RetryEvaluation:
    """纯决策函数的不可变输出。"""

    decision: RetryDecision
    reason_code: RetryReasonCode

    def __post_init__(self) -> None:
        if not isinstance(self.decision, RetryDecision):
            raise TypeError("decision 必须是 RetryDecision")
        if not isinstance(self.reason_code, RetryReasonCode):
            raise TypeError("reason_code 必须是 RetryReasonCode")


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
class RetryAttempt:
    """一次真实 Invoker 进入边界的安全、不可变审计记录。"""

    provider_id: str
    provider_attempt_index: int
    global_call_index: int
    terminal_status: RouteTerminalStatus
    error_code: RetryErrorCode | None
    error_class: ProviderErrorClass | None
    retry_decision: RetryDecision
    retry_reason_code: RetryReasonCode

    def __post_init__(self) -> None:
        provider_id = normalize_provider_id(self.provider_id)
        for name, value in (
            ("provider_attempt_index", self.provider_attempt_index),
            ("global_call_index", self.global_call_index),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ValueError(f"{name} 必须是正整数")
        if not isinstance(self.terminal_status, RouteTerminalStatus):
            raise TypeError("terminal_status 必须是 RouteTerminalStatus")
        if self.terminal_status is RouteTerminalStatus.SKIPPED:
            raise ValueError("RetryAttempt 对应物理调用，不允许 skipped")
        if self.error_code is not None and not isinstance(
            self.error_code, RetryErrorCode
        ):
            raise TypeError("error_code 必须是 RetryErrorCode 或 None")
        if self.error_class is not None and not isinstance(
            self.error_class, ProviderErrorClass
        ):
            raise TypeError("error_class 必须是 ProviderErrorClass 或 None")
        if not isinstance(self.retry_decision, RetryDecision):
            raise TypeError("retry_decision 必须是 RetryDecision")
        if not isinstance(self.retry_reason_code, RetryReasonCode):
            raise TypeError("retry_reason_code 必须是 RetryReasonCode")
        failed = self.terminal_status is RouteTerminalStatus.FAILED
        if failed != (self.error_code is not None):
            raise ValueError("failed RetryAttempt 必须且只能包含错误码")
        if failed != (self.error_class is not None):
            raise ValueError("failed RetryAttempt 必须且只能包含错误分类")
        if self.retry_decision is RetryDecision.RETRY and not failed:
            raise ValueError("只有 failed RetryAttempt 可以请求 retry")
        if self.retry_decision is RetryDecision.STOP and not failed:
            raise ValueError("只有 failed RetryAttempt 可以 stop")
        if self.retry_decision is RetryDecision.SUCCESS and failed:
            raise ValueError("failed RetryAttempt 不得标记 success")
        if (
            self.retry_decision is RetryDecision.FALLBACK
            and self.terminal_status is RouteTerminalStatus.SUCCESS
        ):
            raise ValueError("success RetryAttempt 不得请求 fallback")
        object.__setattr__(self, "provider_id", provider_id)


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
    retry_attempts: tuple[RetryAttempt, ...] = field(
        default=(),
        compare=False,
    )

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
        if not isinstance(self.retry_attempts, tuple) or any(
            not isinstance(attempt, RetryAttempt)
            for attempt in self.retry_attempts
        ):
            raise TypeError("retry_attempts 必须是 RetryAttempt tuple")
        if self.terminal_status is RouteTerminalStatus.SKIPPED:
            if self.retry_attempts:
                raise ValueError("skipped RouteAttempt 不得包含 RetryAttempt")
        elif self.retry_attempts:
            if any(
                attempt.provider_id != provider_id
                for attempt in self.retry_attempts
            ):
                raise ValueError("RetryAttempt 必须属于当前 Provider")
            expected_provider_indexes = tuple(
                range(1, len(self.retry_attempts) + 1)
            )
            if tuple(
                attempt.provider_attempt_index
                for attempt in self.retry_attempts
            ) != expected_provider_indexes:
                raise ValueError(
                    "provider_attempt_index 必须从 1 连续递增"
                )
            global_indexes = tuple(
                attempt.global_call_index
                for attempt in self.retry_attempts
            )
            if any(
                current <= previous
                for previous, current in zip(
                    global_indexes, global_indexes[1:]
                )
            ):
                raise ValueError("global_call_index 必须严格递增")
            if (
                self.retry_attempts[-1].terminal_status
                is not self.terminal_status
            ):
                raise ValueError(
                    "RouteAttempt 终态必须与最后一个 RetryAttempt 一致"
                )
            final_retry_decision = self.retry_attempts[-1].retry_decision
            if final_retry_decision is RetryDecision.RETRY:
                raise ValueError(
                    "RouteAttempt 的最后一个 RetryAttempt 不得继续 retry"
                )
            if (
                final_retry_decision is RetryDecision.FALLBACK
            ) != self.fallback_triggered:
                raise ValueError(
                    "fallback_triggered 必须与最终 RetryDecision 一致"
                )
            if (
                self.selected
                and final_retry_decision is not RetryDecision.SUCCESS
            ):
                raise ValueError(
                    "selected RouteAttempt 必须以 success 决策结束"
                )
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


def classify_provider_error(error: Exception) -> ErrorClassification:
    """只按异常类型分类；不会检查 message、URL、状态正文或动态字段。"""
    if not isinstance(error, Exception):
        raise TypeError("error 必须是 Exception")
    if isinstance(error, ProviderTimeoutError):
        return ErrorClassification(
            RetryErrorCode.TIMEOUT,
            ProviderErrorClass.TRANSIENT,
        )
    if isinstance(error, ProviderNetworkError):
        return ErrorClassification(
            RetryErrorCode.NETWORK_ERROR,
            ProviderErrorClass.TRANSIENT,
        )
    if isinstance(error, ProviderRateLimitError):
        return ErrorClassification(
            RetryErrorCode.RATE_LIMITED,
            ProviderErrorClass.RATE_LIMITED,
        )
    if isinstance(error, ProviderAuthenticationError):
        return ErrorClassification(
            RetryErrorCode.AUTHENTICATION,
            ProviderErrorClass.AUTHENTICATION,
        )
    if isinstance(error, ProviderValidationError):
        return ErrorClassification(
            RetryErrorCode.VALIDATION,
            ProviderErrorClass.VALIDATION,
        )
    if isinstance(error, ProviderParseError):
        return ErrorClassification(
            RetryErrorCode.PROTOCOL,
            ProviderErrorClass.PROTOCOL,
        )
    if isinstance(error, ProviderBlockedError):
        return ErrorClassification(
            RetryErrorCode.BLOCKED,
            ProviderErrorClass.BLOCKED,
        )
    if isinstance(error, ProviderUnavailableError):
        return ErrorClassification(
            RetryErrorCode.PROVIDER_UNAVAILABLE,
            ProviderErrorClass.UNAVAILABLE,
        )
    if isinstance(error, ProviderError):
        return ErrorClassification(
            RetryErrorCode.PROVIDER_FAILED,
            ProviderErrorClass.PERMANENT,
        )
    return ErrorClassification(
        RetryErrorCode.INVOKER_FAILED,
        ProviderErrorClass.UNKNOWN,
    )


def decide_retry(
    classification: ErrorClassification,
    policy: RetryPolicy,
    *,
    provider_attempt_index: int,
    call_budget_remaining: int,
    fallback_available: bool,
) -> RetryEvaluation:
    """根据封闭分类和计数作确定性决策，不执行等待或 I/O。"""
    if not isinstance(classification, ErrorClassification):
        raise TypeError("classification 必须是 ErrorClassification")
    if not isinstance(policy, RetryPolicy):
        raise TypeError("policy 必须是 RetryPolicy")
    if (
        not isinstance(provider_attempt_index, int)
        or isinstance(provider_attempt_index, bool)
        or provider_attempt_index <= 0
    ):
        raise ValueError("provider_attempt_index 必须是正整数")
    if (
        not isinstance(call_budget_remaining, int)
        or isinstance(call_budget_remaining, bool)
        or call_budget_remaining < 0
    ):
        raise ValueError("call_budget_remaining 必须是非负整数")
    if not isinstance(fallback_available, bool):
        raise TypeError("fallback_available 必须是布尔值")

    if classification.error_class is ProviderErrorClass.RATE_LIMITED:
        retryable = policy.retry_on_rate_limit
        retry_reason = (
            RetryReasonCode.RATE_LIMIT_RETRY_ENABLED
            if retryable
            else RetryReasonCode.RATE_LIMIT_RETRY_DISABLED
        )
    elif classification.error_class is ProviderErrorClass.UNKNOWN:
        retryable = policy.retry_on_unknown
        retry_reason = (
            RetryReasonCode.UNKNOWN_RETRY_ENABLED
            if retryable
            else RetryReasonCode.UNKNOWN_RETRY_DISABLED
        )
    else:
        try:
            retryable_code = RetryableErrorCode(
                classification.error_code.value
            )
        except ValueError:
            retryable = False
        else:
            retryable = retryable_code in policy.retryable_error_codes
        retry_reason = (
            RetryReasonCode.RETRYABLE_ERROR
            if retryable
            else RetryReasonCode.NON_RETRYABLE_ERROR
        )

    if retryable and (
        provider_attempt_index < policy.max_attempts_per_provider
    ):
        if call_budget_remaining == 0:
            return RetryEvaluation(
                RetryDecision.STOP,
                RetryReasonCode.CALL_BUDGET_EXHAUSTED,
            )
        return RetryEvaluation(RetryDecision.RETRY, retry_reason)

    if call_budget_remaining == 0 and fallback_available:
        return RetryEvaluation(
            RetryDecision.STOP,
            RetryReasonCode.CALL_BUDGET_EXHAUSTED,
        )

    fallback_allowed = (
        provider_attempt_index == 1
        or policy.allow_fallback_after_retry
    )
    if fallback_available and fallback_allowed:
        reason = (
            RetryReasonCode.MAX_ATTEMPTS_REACHED
            if retryable
            else retry_reason
        )
        return RetryEvaluation(RetryDecision.FALLBACK, reason)
    if fallback_available and not fallback_allowed:
        return RetryEvaluation(
            RetryDecision.STOP,
            RetryReasonCode.RETRY_FALLBACK_DISABLED,
        )
    if retryable and (
        provider_attempt_index >= policy.max_attempts_per_provider
    ):
        return RetryEvaluation(
            RetryDecision.STOP,
            RetryReasonCode.MAX_ATTEMPTS_REACHED,
        )
    return RetryEvaluation(
        RetryDecision.STOP,
        (
            retry_reason
            if retry_reason
            in {
                RetryReasonCode.RATE_LIMIT_RETRY_DISABLED,
                RetryReasonCode.UNKNOWN_RETRY_DISABLED,
                RetryReasonCode.NON_RETRYABLE_ERROR,
            }
            else RetryReasonCode.NO_FALLBACK_AVAILABLE
        ),
    )


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
    call_budget_total: int | None = None

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
        call_budget_total = self.call_budget_total
        if call_budget_total is None:
            call_budget_total = (
                self.call_budget_used + self.call_budget_remaining
            )
        elif (
            not isinstance(call_budget_total, int)
            or isinstance(call_budget_total, bool)
            or call_budget_total < 0
        ):
            raise ValueError("call_budget_total 必须是非负整数或 None")
        if self.call_budget_used + self.call_budget_remaining != (
            call_budget_total
        ):
            raise ValueError(
                "call_budget_used + call_budget_remaining 必须等于 call_budget_total"
            )
        object.__setattr__(self, "call_budget_total", call_budget_total)
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
                len(item.retry_attempts)
                if item.retry_attempts
                else int(
                    item.terminal_status
                    is not RouteTerminalStatus.SKIPPED
                )
                for item in self.attempts
            )
            if self.call_budget_used != actual_calls:
                raise ValueError("call_budget_used 必须等于物理调用记录数")
            retry_call_indexes = tuple(
                retry_attempt.global_call_index
                for route_attempt in self.attempts
                for retry_attempt in route_attempt.retry_attempts
            )
            if retry_call_indexes and retry_call_indexes != tuple(
                range(1, len(retry_call_indexes) + 1)
            ):
                raise ValueError(
                    "global_call_index 必须在完整路由中从 1 连续递增"
                )
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

    __slots__ = (
        "_registry",
        "_policy",
        "_invoker",
        "_evaluator",
        "_retry_policy",
    )

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        policy: RoutePolicy,
        invoker: ProviderInvoker[T],
        evaluator: ResultEvaluator[T],
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("registry 必须是 ProviderRegistry")
        if not isinstance(policy, RoutePolicy):
            raise TypeError("policy 必须是 RoutePolicy")
        if not callable(invoker):
            raise TypeError("invoker 必须可调用")
        if not callable(evaluator):
            raise TypeError("evaluator 必须可调用")
        if retry_policy is not None and not isinstance(
            retry_policy, RetryPolicy
        ):
            raise TypeError("retry_policy 必须是 RetryPolicy 或 None")
        self._registry = registry
        self._policy = policy
        self._invoker = invoker
        self._evaluator = evaluator
        self._retry_policy = retry_policy or RetryPolicy()

    def execute(self) -> RouteResult[T]:
        """执行一次 route；Retry 与 Fallback 共享物理调用预算。"""
        policy = self._policy
        if policy.mode is DataRouteMode.LEGACY:
            return RouteResult(
                selected_provider=None,
                attempted_provider_ids=(),
                used_legacy_fallback=False,
                status=RouteStatus.LEGACY,
                terminal_status=RouteTerminalStatus.SKIPPED,
                call_budget_remaining=policy.max_call_budget,
                call_budget_total=policy.max_call_budget,
            )

        attempts: list[RouteAttempt] = []
        retained_results: list[ProviderResult[T]] = []
        call_budget_used = 0
        final_error = RouteErrorCode.NO_ELIGIBLE_PROVIDER
        final_terminal = RouteTerminalStatus.SKIPPED
        required_stage = _STAGE_BY_MODE[policy.mode]
        candidate_count = len(policy.candidate_provider_ids)
        stop_routing = False

        for position, provider_id in enumerate(policy.candidate_provider_ids):
            if stop_routing:
                break
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

            retry_attempts: list[RetryAttempt] = []
            provider_attempt_index = 0

            while True:
                # 在进入 Invoker 前先扣减；异常、终止信号和 Retry 都不能绕过。
                call_budget_used += 1
                provider_attempt_index += 1
                global_call_index = call_budget_used
                budget_remaining = policy.max_call_budget - call_budget_used
                fallback_available = position + 1 < candidate_count

                try:
                    provider_result = self._invoker(registration)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    classification = classify_provider_error(exc)
                    retry_evaluation = decide_retry(
                        classification,
                        self._retry_policy,
                        provider_attempt_index=provider_attempt_index,
                        call_budget_remaining=budget_remaining,
                        fallback_available=fallback_available,
                    )
                    retry_attempts.append(
                        RetryAttempt(
                            provider_id=provider_id,
                            provider_attempt_index=provider_attempt_index,
                            global_call_index=global_call_index,
                            terminal_status=RouteTerminalStatus.FAILED,
                            error_code=classification.error_code,
                            error_class=classification.error_class,
                            retry_decision=retry_evaluation.decision,
                            retry_reason_code=retry_evaluation.reason_code,
                        )
                    )
                    if retry_evaluation.decision is RetryDecision.RETRY:
                        continue
                    route_error_code = (
                        exc.code
                        if isinstance(exc, ProviderError) and exc.code
                        else (
                            RouteErrorCode.PROVIDER_FAILED.value
                            if isinstance(exc, ProviderError)
                            else RouteErrorCode.INVOKER_FAILED.value
                        )
                    )
                    attempts.append(
                        RouteAttempt(
                            provider_id=provider_id,
                            attempt_index=attempt_index,
                            terminal_status=RouteTerminalStatus.FAILED,
                            item_count=0,
                            issue_count=0,
                            error_code=route_error_code,
                            selected=False,
                            fallback_triggered=(
                                retry_evaluation.decision
                                is RetryDecision.FALLBACK
                            ),
                            retry_attempts=tuple(retry_attempts),
                        )
                    )
                    final_error = (
                        RouteErrorCode.CALL_BUDGET_EXHAUSTED
                        if retry_evaluation.reason_code
                        is RetryReasonCode.CALL_BUDGET_EXHAUSTED
                        else (
                            RouteErrorCode.PROVIDER_FAILED
                            if isinstance(exc, ProviderError)
                            else RouteErrorCode.INVOKER_FAILED
                        )
                    )
                    final_terminal = RouteTerminalStatus.FAILED
                    if (
                        retry_evaluation.decision
                        is RetryDecision.FALLBACK
                    ):
                        break
                    if (
                        retry_evaluation.reason_code
                        is RetryReasonCode.CALL_BUDGET_EXHAUSTED
                        and fallback_available
                    ):
                        # 后续候选只会形成 budget-exhausted/skipped 记录。
                        break
                    stop_routing = True
                    break

                provider_failure = _validate_provider_execution(
                    provider_result,
                    registration,
                    self._evaluator,
                )
                if isinstance(provider_failure, _ExecutionFailure):
                    retry_evaluation = decide_retry(
                        provider_failure.classification,
                        self._retry_policy,
                        provider_attempt_index=provider_attempt_index,
                        call_budget_remaining=budget_remaining,
                        fallback_available=fallback_available,
                    )
                    retry_attempts.append(
                        RetryAttempt(
                            provider_id=provider_id,
                            provider_attempt_index=provider_attempt_index,
                            global_call_index=global_call_index,
                            terminal_status=RouteTerminalStatus.FAILED,
                            error_code=provider_failure.classification.error_code,
                            error_class=provider_failure.classification.error_class,
                            retry_decision=retry_evaluation.decision,
                            retry_reason_code=retry_evaluation.reason_code,
                        )
                    )
                    attempts.append(
                        RouteAttempt(
                            provider_id=provider_id,
                            attempt_index=attempt_index,
                            terminal_status=RouteTerminalStatus.FAILED,
                            item_count=provider_failure.item_count,
                            issue_count=provider_failure.issue_count,
                            error_code=provider_failure.route_error.value,
                            selected=False,
                            fallback_triggered=(
                                retry_evaluation.decision
                                is RetryDecision.FALLBACK
                            ),
                            retry_attempts=tuple(retry_attempts),
                        )
                    )
                    final_error = (
                        RouteErrorCode.CALL_BUDGET_EXHAUSTED
                        if retry_evaluation.reason_code
                        is RetryReasonCode.CALL_BUDGET_EXHAUSTED
                        else provider_failure.route_error
                    )
                    final_terminal = RouteTerminalStatus.FAILED
                    if (
                        retry_evaluation.decision
                        is RetryDecision.FALLBACK
                    ):
                        break
                    if (
                        retry_evaluation.reason_code
                        is RetryReasonCode.CALL_BUDGET_EXHAUSTED
                        and fallback_available
                    ):
                        break
                    stop_routing = True
                    break

                evaluation = provider_failure
                retained_results.append(provider_result)
                fallback_requested = (
                    evaluation.terminal_status
                    is RouteTerminalStatus.EMPTY
                    and policy.fallback_on_empty
                ) or (
                    evaluation.terminal_status
                    is RouteTerminalStatus.PARTIAL
                    and evaluation.fallback_decision
                    is FallbackDecision.CONTINUE
                )
                should_fallback = (
                    fallback_requested
                    and fallback_available
                    and budget_remaining > 0
                )
                retry_attempts.append(
                    RetryAttempt(
                        provider_id=provider_id,
                        provider_attempt_index=provider_attempt_index,
                        global_call_index=global_call_index,
                        terminal_status=evaluation.terminal_status,
                        error_code=None,
                        error_class=None,
                        retry_decision=(
                            RetryDecision.FALLBACK
                            if should_fallback
                            else RetryDecision.SUCCESS
                        ),
                        retry_reason_code=(
                            RetryReasonCode.RESULT_FALLBACK_REQUESTED
                            if should_fallback
                            else RetryReasonCode.RESULT_ACCEPTED
                        ),
                    )
                )
                attempts.append(
                    RouteAttempt(
                        provider_id=provider_id,
                        attempt_index=attempt_index,
                        terminal_status=evaluation.terminal_status,
                        item_count=len(provider_result.items),
                        issue_count=len(provider_result.issues),
                        error_code=None,
                        selected=not should_fallback,
                        fallback_triggered=should_fallback,
                        retry_attempts=tuple(retry_attempts),
                    )
                )
                if should_fallback:
                    final_terminal = evaluation.terminal_status
                    break
                return RouteResult(
                    selected_provider=registration,
                    attempted_provider_ids=tuple(
                        item.provider_id for item in attempts
                    ),
                    used_legacy_fallback=False,
                    status=RouteStatus.SELECTED,
                    provider_result=provider_result,
                    attempts=tuple(attempts),
                    terminal_status=evaluation.terminal_status,
                    call_budget_used=call_budget_used,
                    call_budget_remaining=budget_remaining,
                    call_budget_total=policy.max_call_budget,
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
            call_budget_total=policy.max_call_budget,
            retained_results=tuple(retained_results),
        )


@dataclass(frozen=True, slots=True)
class _ExecutionFailure:
    route_error: RouteErrorCode
    classification: ErrorClassification
    item_count: int = 0
    issue_count: int = 0


def _validate_provider_execution(
    provider_result: object,
    registration: ProviderRegistration,
    evaluator: ResultEvaluator[T],
) -> ResultEvaluation | _ExecutionFailure:
    if (
        not isinstance(provider_result, ProviderResult)
        or provider_result.provider != registration.descriptor
    ):
        return _ExecutionFailure(
            route_error=RouteErrorCode.INVALID_PROVIDER_RESULT,
            classification=ErrorClassification(
                RetryErrorCode.INVALID_PROVIDER_RESULT,
                ProviderErrorClass.PROTOCOL,
            ),
        )
    try:
        evaluation = evaluator(provider_result)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return _ExecutionFailure(
            route_error=RouteErrorCode.EVALUATOR_FAILED,
            classification=ErrorClassification(
                RetryErrorCode.EVALUATOR_FAILED,
                ProviderErrorClass.PERMANENT,
            ),
            item_count=len(provider_result.items),
            issue_count=len(provider_result.issues),
        )
    if not isinstance(evaluation, ResultEvaluation):
        return _ExecutionFailure(
            route_error=RouteErrorCode.INVALID_EVALUATION,
            classification=ErrorClassification(
                RetryErrorCode.INVALID_EVALUATION,
                ProviderErrorClass.PROTOCOL,
            ),
            item_count=len(provider_result.items),
            issue_count=len(provider_result.issues),
        )
    return evaluation


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
    retry_policy: RetryPolicy | None = None,
) -> RouteResult[T]:
    """一次性便捷入口；等价于构造 ProviderRouter 后调用 execute()。"""
    return ProviderRouter(
        registry=registry,
        policy=policy,
        invoker=invoker,
        evaluator=evaluator,
        retry_policy=retry_policy,
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
