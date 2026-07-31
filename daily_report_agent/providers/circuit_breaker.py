"""Provider Circuit Breaker 的纯离线、确定性状态转换契约。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from daily_report_agent.models.news import is_timezone_aware

from .contracts import normalize_provider_id, normalize_provider_operation
from .routing import ErrorClassification, ProviderErrorClass, RetryErrorCode


_MAX_FAILURE_THRESHOLD = 1_000_000
_MAX_OPEN_DURATION = timedelta(days=365)


class CircuitBreakerState(str, Enum):
    """Circuit Breaker 的封闭状态。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitDecision(str, Enum):
    """调用边界可执行的封闭决策。"""

    ALLOW = "allow"
    SKIP = "skip"
    PROBE = "probe"


class CircuitTransitionReason(str, Enum):
    """状态转换的固定、安全原因码。"""

    CLOSED_ALLOW = "closed_allow"
    FAILURE_RECORDED = "failure_recorded"
    FAILURE_THRESHOLD_REACHED = "failure_threshold_reached"
    IMMEDIATE_OPEN = "immediate_open"
    OPEN_WINDOW_ACTIVE = "open_window_active"
    OPEN_WINDOW_ELAPSED = "open_window_elapsed"
    HALF_OPEN_PROBE_ALLOWED = "half_open_probe_allowed"
    HALF_OPEN_PROBE_BUSY = "half_open_probe_busy"
    HALF_OPEN_PROBE_SUCCEEDED = "half_open_probe_succeeded"
    HALF_OPEN_PROBE_FAILED = "half_open_probe_failed"
    SUCCESS_RESET = "success_reset"
    NEUTRAL_OUTCOME = "neutral_outcome"


class CircuitOutcome(str, Enum):
    """一次调用结果对 Provider 健康状态的封闭影响。"""

    SUCCESS = "success"
    COUNTED_FAILURE = "counted_failure"
    IMMEDIATE_OPEN = "immediate_open"
    NEUTRAL = "neutral"


class CircuitOutcomeOrigin(str, Enum):
    """结果所属的执行边界；非 Provider 边界默认不影响 Provider 健康。"""

    PROVIDER = "provider"
    EVALUATOR = "evaluator"
    MERGER = "merger"
    CALLER = "caller"
    INPUT = "input"


_DEFAULT_COUNTED_ERROR_CLASSES = frozenset(
    {
        ProviderErrorClass.TRANSIENT,
        ProviderErrorClass.UNAVAILABLE,
        ProviderErrorClass.RATE_LIMITED,
        ProviderErrorClass.PERMANENT,
        ProviderErrorClass.UNKNOWN,
    }
)
_DEFAULT_IMMEDIATE_OPEN_ERROR_CLASSES = frozenset(
    {
        ProviderErrorClass.BLOCKED,
        ProviderErrorClass.AUTHENTICATION,
        ProviderErrorClass.PROTOCOL,
    }
)
_ALLOWED_COUNTED_ERROR_CLASSES = _DEFAULT_COUNTED_ERROR_CLASSES
_ALLOWED_IMMEDIATE_OPEN_ERROR_CLASSES = _DEFAULT_IMMEDIATE_OPEN_ERROR_CLASSES


def _validate_aware_datetime(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} 必须是 datetime")
    if not is_timezone_aware(value):
        raise ValueError(f"{name} 必须是 timezone-aware datetime")
    return value


@dataclass(frozen=True, slots=True)
class CircuitBreakerKey:
    """熔断粒度固定为 provider_id + operation。"""

    provider_id: str
    operation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            normalize_provider_id(self.provider_id),
        )
        object.__setattr__(
            self,
            "operation",
            normalize_provider_operation(self.operation),
        )


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    """纯离线熔断策略；第一版只允许一个 half-open 探针。"""

    failure_threshold: int = 3
    open_duration: timedelta = timedelta(minutes=5)
    half_open_max_probes: int = 1
    counted_error_classes: frozenset[ProviderErrorClass] = (
        _DEFAULT_COUNTED_ERROR_CLASSES
    )
    immediate_open_error_classes: frozenset[ProviderErrorClass] = (
        _DEFAULT_IMMEDIATE_OPEN_ERROR_CLASSES
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.failure_threshold, int)
            or isinstance(self.failure_threshold, bool)
        ):
            raise TypeError("failure_threshold 必须是整数")
        if not 1 <= self.failure_threshold <= _MAX_FAILURE_THRESHOLD:
            raise ValueError(
                "failure_threshold 必须位于 1 到 1000000 之间"
            )
        if not isinstance(self.open_duration, timedelta):
            raise TypeError("open_duration 必须是 timedelta")
        if not timedelta(0) < self.open_duration <= _MAX_OPEN_DURATION:
            raise ValueError("open_duration 必须大于 0 且不超过 365 天")
        if (
            not isinstance(self.half_open_max_probes, int)
            or isinstance(self.half_open_max_probes, bool)
        ):
            raise TypeError("half_open_max_probes 必须是整数")
        if self.half_open_max_probes != 1:
            raise ValueError("第一版 half_open_max_probes 只允许 1")
        if not isinstance(self.counted_error_classes, frozenset):
            raise TypeError("counted_error_classes 必须是 frozenset")
        if not isinstance(self.immediate_open_error_classes, frozenset):
            raise TypeError(
                "immediate_open_error_classes 必须是 frozenset"
            )
        if any(
            not isinstance(item, ProviderErrorClass)
            for item in self.counted_error_classes
        ):
            raise TypeError(
                "counted_error_classes 只能包含 ProviderErrorClass"
            )
        if any(
            not isinstance(item, ProviderErrorClass)
            for item in self.immediate_open_error_classes
        ):
            raise TypeError(
                "immediate_open_error_classes 只能包含 ProviderErrorClass"
            )
        if not self.counted_error_classes <= _ALLOWED_COUNTED_ERROR_CLASSES:
            raise ValueError("counted_error_classes 包含不允许计数的分类")
        if not (
            self.immediate_open_error_classes
            <= _ALLOWED_IMMEDIATE_OPEN_ERROR_CLASSES
        ):
            raise ValueError(
                "immediate_open_error_classes 包含不允许立即打开的分类"
            )
        if (
            self.counted_error_classes
            & self.immediate_open_error_classes
        ):
            raise ValueError("计数分类与立即打开分类不得重叠")


@dataclass(frozen=True, slots=True)
class CircuitBreakerSnapshot:
    """单个 provider_id + operation 的不可变状态快照。"""

    key: CircuitBreakerKey
    state: CircuitBreakerState
    consecutive_failures: int
    opened_at: datetime | None
    open_until: datetime | None
    last_transition_at: datetime
    last_failure_at: datetime | None
    last_success_at: datetime | None
    last_error_code: RetryErrorCode | None
    half_open_probe_active: bool

    def __post_init__(self) -> None:
        if not isinstance(self.key, CircuitBreakerKey):
            raise TypeError("key 必须是 CircuitBreakerKey")
        if not isinstance(self.state, CircuitBreakerState):
            raise TypeError("state 必须是 CircuitBreakerState")
        if (
            not isinstance(self.consecutive_failures, int)
            or isinstance(self.consecutive_failures, bool)
        ):
            raise TypeError("consecutive_failures 必须是整数")
        if self.consecutive_failures < 0:
            raise ValueError("consecutive_failures 不得为负")
        last_transition_at = _validate_aware_datetime(
            "last_transition_at",
            self.last_transition_at,
        )
        for name, value in (
            ("opened_at", self.opened_at),
            ("open_until", self.open_until),
            ("last_failure_at", self.last_failure_at),
            ("last_success_at", self.last_success_at),
        ):
            if value is not None:
                _validate_aware_datetime(name, value)
        if self.last_error_code is not None and not isinstance(
            self.last_error_code,
            RetryErrorCode,
        ):
            raise TypeError(
                "last_error_code 必须是 RetryErrorCode 或 None"
            )
        if not isinstance(self.half_open_probe_active, bool):
            raise TypeError("half_open_probe_active 必须是布尔值")
        if self.consecutive_failures == 0 and self.last_error_code is not None:
            raise ValueError("无连续失败时不得保留 last_error_code")
        if self.consecutive_failures > 0 and (
            self.last_failure_at is None or self.last_error_code is None
        ):
            raise ValueError("连续失败必须包含失败时间与安全 error_code")
        if self.state is CircuitBreakerState.CLOSED:
            if self.opened_at is not None or self.open_until is not None:
                raise ValueError("closed 状态不得保留打开窗口")
            if self.half_open_probe_active:
                raise ValueError("closed 状态不得保留 active probe")
        else:
            if self.opened_at is None or self.open_until is None:
                raise ValueError("open/half_open 状态必须包含打开窗口")
            if self.open_until <= self.opened_at:
                raise ValueError("open_until 必须晚于 opened_at")
        if (
            self.state is CircuitBreakerState.OPEN
            and self.half_open_probe_active
        ):
            raise ValueError("open 状态不得包含 active probe")
        if (
            self.state is CircuitBreakerState.HALF_OPEN
            and self.open_until is not None
            and last_transition_at < self.open_until
        ):
            raise ValueError("half_open 不得早于 open_until")
        if self.opened_at is not None and self.opened_at > last_transition_at:
            raise ValueError("opened_at 不得晚于 last_transition_at")
        for name, value in (
            ("last_failure_at", self.last_failure_at),
            ("last_success_at", self.last_success_at),
        ):
            if value is not None and value > last_transition_at:
                raise ValueError(f"{name} 不得晚于 last_transition_at")


@dataclass(frozen=True, slots=True)
class CircuitTransition:
    """一次预检查或结果记录的不可变转换。"""

    before: CircuitBreakerSnapshot
    after: CircuitBreakerSnapshot
    decision: CircuitDecision
    reason: CircuitTransitionReason
    changed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.before, CircuitBreakerSnapshot):
            raise TypeError("before 必须是 CircuitBreakerSnapshot")
        if not isinstance(self.after, CircuitBreakerSnapshot):
            raise TypeError("after 必须是 CircuitBreakerSnapshot")
        if self.before.key != self.after.key:
            raise ValueError("CircuitTransition 不得改变 key")
        if not isinstance(self.decision, CircuitDecision):
            raise TypeError("decision 必须是 CircuitDecision")
        if not isinstance(self.reason, CircuitTransitionReason):
            raise TypeError("reason 必须是 CircuitTransitionReason")
        if not isinstance(self.changed, bool):
            raise TypeError("changed 必须是布尔值")
        if self.changed != (self.before != self.after):
            raise ValueError("changed 必须与 before/after 差异一致")


def create_closed_circuit(
    key: CircuitBreakerKey,
    *,
    now: datetime,
) -> CircuitBreakerSnapshot:
    """用显式时间创建不含隐式时钟读取的 closed 初始快照。"""
    if not isinstance(key, CircuitBreakerKey):
        raise TypeError("key 必须是 CircuitBreakerKey")
    _validate_aware_datetime("now", now)
    return CircuitBreakerSnapshot(
        key=key,
        state=CircuitBreakerState.CLOSED,
        consecutive_failures=0,
        opened_at=None,
        open_until=None,
        last_transition_at=now,
        last_failure_at=None,
        last_success_at=None,
        last_error_code=None,
        half_open_probe_active=False,
    )


def _validate_now(
    snapshot: CircuitBreakerSnapshot,
    now: datetime,
) -> datetime:
    if not isinstance(snapshot, CircuitBreakerSnapshot):
        raise TypeError("snapshot 必须是 CircuitBreakerSnapshot")
    _validate_aware_datetime("now", now)
    if now < snapshot.last_transition_at:
        raise ValueError("now 不得早于 snapshot.last_transition_at")
    return now


def _decision_for_snapshot(
    snapshot: CircuitBreakerSnapshot,
) -> CircuitDecision:
    if snapshot.state is CircuitBreakerState.CLOSED:
        return CircuitDecision.ALLOW
    if snapshot.state is CircuitBreakerState.OPEN:
        return CircuitDecision.SKIP
    if snapshot.half_open_probe_active:
        return CircuitDecision.SKIP
    return CircuitDecision.PROBE


def _transition(
    before: CircuitBreakerSnapshot,
    after: CircuitBreakerSnapshot,
    *,
    decision: CircuitDecision,
    reason: CircuitTransitionReason,
) -> CircuitTransition:
    return CircuitTransition(
        before=before,
        after=after,
        decision=decision,
        reason=reason,
        changed=before != after,
    )


def evaluate_circuit(
    snapshot: CircuitBreakerSnapshot,
    *,
    now: datetime,
) -> CircuitTransition:
    """在 Invoker 前评估状态；OPEN skip 未来不得消耗调用预算。"""
    _validate_now(snapshot, now)
    if snapshot.state is CircuitBreakerState.CLOSED:
        return _transition(
            snapshot,
            snapshot,
            decision=CircuitDecision.ALLOW,
            reason=CircuitTransitionReason.CLOSED_ALLOW,
        )
    if snapshot.state is CircuitBreakerState.OPEN:
        assert snapshot.open_until is not None
        if now < snapshot.open_until:
            return _transition(
                snapshot,
                snapshot,
                decision=CircuitDecision.SKIP,
                reason=CircuitTransitionReason.OPEN_WINDOW_ACTIVE,
            )
        after = replace(
            snapshot,
            state=CircuitBreakerState.HALF_OPEN,
            last_transition_at=now,
            half_open_probe_active=True,
        )
        return _transition(
            snapshot,
            after,
            decision=CircuitDecision.PROBE,
            reason=CircuitTransitionReason.OPEN_WINDOW_ELAPSED,
        )
    if snapshot.half_open_probe_active:
        return _transition(
            snapshot,
            snapshot,
            decision=CircuitDecision.SKIP,
            reason=CircuitTransitionReason.HALF_OPEN_PROBE_BUSY,
        )
    after = replace(
        snapshot,
        last_transition_at=now,
        half_open_probe_active=True,
    )
    return _transition(
        snapshot,
        after,
        decision=CircuitDecision.PROBE,
        reason=CircuitTransitionReason.HALF_OPEN_PROBE_ALLOWED,
    )


def classify_circuit_outcome(
    classification: ErrorClassification,
    policy: CircuitBreakerPolicy,
    *,
    origin: CircuitOutcomeOrigin = CircuitOutcomeOrigin.PROVIDER,
) -> CircuitOutcome:
    """把 M1-03 分类纯映射为 counted/immediate/neutral。"""
    if not isinstance(classification, ErrorClassification):
        raise TypeError("classification 必须是 ErrorClassification")
    if not isinstance(policy, CircuitBreakerPolicy):
        raise TypeError("policy 必须是 CircuitBreakerPolicy")
    if not isinstance(origin, CircuitOutcomeOrigin):
        raise TypeError("origin 必须是 CircuitOutcomeOrigin")
    if origin is not CircuitOutcomeOrigin.PROVIDER:
        return CircuitOutcome.NEUTRAL
    if classification.error_class in policy.immediate_open_error_classes:
        return CircuitOutcome.IMMEDIATE_OPEN
    if classification.error_class in policy.counted_error_classes:
        return CircuitOutcome.COUNTED_FAILURE
    return CircuitOutcome.NEUTRAL


def _open_snapshot(
    snapshot: CircuitBreakerSnapshot,
    policy: CircuitBreakerPolicy,
    *,
    now: datetime,
    error_code: RetryErrorCode,
    consecutive_failures: int,
) -> CircuitBreakerSnapshot:
    try:
        open_until = now + policy.open_duration
    except OverflowError as exc:
        raise ValueError("now 与 open_duration 超出 datetime 范围") from exc
    return replace(
        snapshot,
        state=CircuitBreakerState.OPEN,
        consecutive_failures=consecutive_failures,
        opened_at=now,
        open_until=open_until,
        last_transition_at=now,
        last_failure_at=now,
        last_error_code=error_code,
        half_open_probe_active=False,
    )


def record_circuit_success(
    snapshot: CircuitBreakerSnapshot,
    *,
    now: datetime,
) -> CircuitTransition:
    """记录 Provider 成功；half-open 成功关闭并清除打开窗口。"""
    _validate_now(snapshot, now)
    if snapshot.state is CircuitBreakerState.OPEN:
        raise ValueError("open 状态不得记录未授权的 Provider success")
    if (
        snapshot.state is CircuitBreakerState.HALF_OPEN
        and not snapshot.half_open_probe_active
    ):
        raise ValueError("half_open success 必须对应 active probe")
    reason = (
        CircuitTransitionReason.HALF_OPEN_PROBE_SUCCEEDED
        if snapshot.state is CircuitBreakerState.HALF_OPEN
        else CircuitTransitionReason.SUCCESS_RESET
    )
    after = replace(
        snapshot,
        state=CircuitBreakerState.CLOSED,
        consecutive_failures=0,
        opened_at=None,
        open_until=None,
        last_transition_at=now,
        last_success_at=now,
        last_error_code=None,
        half_open_probe_active=False,
    )
    return _transition(
        snapshot,
        after,
        decision=CircuitDecision.ALLOW,
        reason=reason,
    )


def record_circuit_outcome(
    snapshot: CircuitBreakerSnapshot,
    policy: CircuitBreakerPolicy,
    outcome: CircuitOutcome,
    *,
    now: datetime,
    error_code: RetryErrorCode | None = None,
) -> CircuitTransition:
    """记录显式 outcome；不调用时钟、Provider、数据库或日志。"""
    _validate_now(snapshot, now)
    if not isinstance(policy, CircuitBreakerPolicy):
        raise TypeError("policy 必须是 CircuitBreakerPolicy")
    if not isinstance(outcome, CircuitOutcome):
        raise TypeError("outcome 必须是 CircuitOutcome")
    if error_code is not None and not isinstance(error_code, RetryErrorCode):
        raise TypeError("error_code 必须是 RetryErrorCode 或 None")
    if outcome is CircuitOutcome.SUCCESS:
        if error_code is not None:
            raise ValueError("success outcome 不得包含 error_code")
        return record_circuit_success(snapshot, now=now)
    if outcome in {
        CircuitOutcome.COUNTED_FAILURE,
        CircuitOutcome.IMMEDIATE_OPEN,
    } and error_code is None:
        raise ValueError("failure outcome 必须包含安全 error_code")
    if outcome is CircuitOutcome.NEUTRAL:
        if error_code is not None:
            raise ValueError("neutral outcome 不得包含 error_code")
        if snapshot.state is CircuitBreakerState.HALF_OPEN:
            after = replace(
                snapshot,
                last_transition_at=now,
                half_open_probe_active=False,
            )
        else:
            after = snapshot
        return _transition(
            snapshot,
            after,
            decision=_decision_for_snapshot(after),
            reason=CircuitTransitionReason.NEUTRAL_OUTCOME,
        )
    if snapshot.state is CircuitBreakerState.OPEN:
        raise ValueError("open 状态不得记录未授权的 Provider failure")
    if snapshot.state is CircuitBreakerState.HALF_OPEN:
        if not snapshot.half_open_probe_active:
            raise ValueError("half_open failure 必须对应 active probe")
        after = _open_snapshot(
            snapshot,
            policy,
            now=now,
            error_code=error_code,
            consecutive_failures=snapshot.consecutive_failures + 1,
        )
        return _transition(
            snapshot,
            after,
            decision=CircuitDecision.SKIP,
            reason=CircuitTransitionReason.HALF_OPEN_PROBE_FAILED,
        )

    consecutive_failures = snapshot.consecutive_failures + 1
    if outcome is CircuitOutcome.IMMEDIATE_OPEN:
        after = _open_snapshot(
            snapshot,
            policy,
            now=now,
            error_code=error_code,
            consecutive_failures=consecutive_failures,
        )
        reason = CircuitTransitionReason.IMMEDIATE_OPEN
        decision = CircuitDecision.SKIP
    elif consecutive_failures >= policy.failure_threshold:
        after = _open_snapshot(
            snapshot,
            policy,
            now=now,
            error_code=error_code,
            consecutive_failures=consecutive_failures,
        )
        reason = CircuitTransitionReason.FAILURE_THRESHOLD_REACHED
        decision = CircuitDecision.SKIP
    else:
        after = replace(
            snapshot,
            consecutive_failures=consecutive_failures,
            last_transition_at=now,
            last_failure_at=now,
            last_error_code=error_code,
        )
        reason = CircuitTransitionReason.FAILURE_RECORDED
        decision = CircuitDecision.ALLOW
    return _transition(
        snapshot,
        after,
        decision=decision,
        reason=reason,
    )


def record_circuit_failure(
    snapshot: CircuitBreakerSnapshot,
    policy: CircuitBreakerPolicy,
    classification: ErrorClassification,
    *,
    now: datetime,
    origin: CircuitOutcomeOrigin = CircuitOutcomeOrigin.PROVIDER,
) -> CircuitTransition:
    """分类并记录失败；不会读取异常正文或修改 M1-03 Retry。"""
    outcome = classify_circuit_outcome(
        classification,
        policy,
        origin=origin,
    )
    return record_circuit_outcome(
        snapshot,
        policy,
        outcome,
        now=now,
        error_code=(
            classification.error_code
            if outcome is not CircuitOutcome.NEUTRAL
            else None
        ),
    )
