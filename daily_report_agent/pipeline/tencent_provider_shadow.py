"""Tencent ProviderRouter Shadow 编排；默认关闭且不进入正式分析链路。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from daily_report_agent.config import (
    ProviderRoutingSettings,
    TencentQuoteShadowSettings,
    parse_tencent_quote_shadow_settings,
)
from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import is_timezone_aware
from daily_report_agent.models.security import Security
from daily_report_agent.providers.circuit_breaker import (
    CircuitBreakerKey,
    CircuitBreakerPolicy,
    CircuitDecision,
    CircuitOutcomeOrigin,
)
from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderResult,
)
from daily_report_agent.providers.errors import ProviderValidationError
from daily_report_agent.providers.routing import (
    DataRouteMode,
    FallbackDecision,
    ProviderErrorClass,
    ProviderInvoker,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRouter,
    ProviderRuntimeStage,
    ResultEvaluation,
    ResultEvaluator,
    RetryPolicy,
    RouteErrorCode,
    RoutePolicy,
    RouteResult,
    RouteTerminalStatus,
)
from daily_report_agent.providers.telemetry import (
    ProviderMetricEmitter,
    ProviderMetricEvent,
    ProviderMetricStatus,
    emit_provider_metric_safely,
    log_provider_metric,
)
from daily_report_agent.providers.tencent.constants import (
    TENCENT_QUOTE_DESCRIPTOR,
)
from daily_report_agent.providers.tencent.symbols import to_tencent_symbol


TENCENT_SHADOW_OPERATION = "fetch_quotes"
TENCENT_SHADOW_KEY = CircuitBreakerKey(
    TENCENT_QUOTE_DESCRIPTOR.provider_id,
    TENCENT_SHADOW_OPERATION,
)
_MAX_SINGLE_REQUEST_SYMBOLS = 5


class ProviderShadowGateErrorCode(str, Enum):
    """可在任何业务副作用前安全外显的 Shadow 门禁错误码。"""

    STAGE_NOT_ENABLED = "route_stage_not_enabled"
    SHADOW_NOT_ENABLED = "provider_shadow_not_enabled"
    CLI_GATE_REQUIRED = "provider_shadow_cli_gate_required"
    DRY_RUN_REJECTED = "provider_shadow_dry_run_rejected"
    DATABASE_PATH_REQUIRED = "provider_shadow_database_path_required"
    DATABASE_PATH_INVALID = "provider_shadow_database_path_invalid"
    DATABASE_PATH_CONFLICT = "provider_shadow_database_path_conflict"
    CALL_BUDGET_INVALID = "provider_shadow_call_budget_invalid"
    CANDIDATES_INVALID = "provider_shadow_candidates_invalid"


class ProviderShadowGateError(ValueError):
    """不包含文件路径、配置正文或凭据的门禁异常。"""

    def __init__(
        self,
        code: ProviderShadowGateErrorCode,
        safe_message: str,
    ) -> None:
        if not isinstance(code, ProviderShadowGateErrorCode):
            raise TypeError("code 必须是 ProviderShadowGateErrorCode")
        if not isinstance(safe_message, str) or not safe_message.strip():
            raise ValueError("safe_message 不能为空")
        self.code = code
        self.safe_message = safe_message.strip()
        super().__init__(self.safe_message)


@dataclass(frozen=True, slots=True)
class TencentProviderShadowGate:
    """全部门禁通过后的纯配置快照。"""

    routing: ProviderRoutingSettings
    quote: TencentQuoteShadowSettings
    database_path: Path


def _resolve_config_path(config_path: str, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(config_path).resolve().parent / candidate
    return candidate.resolve(strict=False)


def _formal_database_path(config: dict, config_path: str) -> Path:
    storage = config.get("storage")
    if storage is None:
        configured = "data/agent.db"
    else:
        if not isinstance(storage, dict):
            raise ValueError("storage 配置必须是 YAML 映射")
        configured = storage.get("path", "data/agent.db")
        if not isinstance(configured, str) or not configured.strip():
            raise ValueError("storage.path 必须是非空字符串")
        configured = configured.strip()
    return _resolve_config_path(config_path, configured)


def validate_tencent_provider_shadow_gate(
    *,
    config: dict,
    config_path: str,
    routing: ProviderRoutingSettings,
    allow_provider_shadow: bool,
    dry_run: bool,
) -> TencentProviderShadowGate | None:
    """纯门禁检查；不会读取环境、创建目录、打开数据库或导入在线 Transport。"""
    if not isinstance(config, dict):
        raise TypeError("config 必须是字典")
    if not isinstance(routing, ProviderRoutingSettings):
        raise TypeError("routing 必须是 ProviderRoutingSettings")
    if not isinstance(allow_provider_shadow, bool):
        raise TypeError("allow_provider_shadow 必须是布尔值")
    if not isinstance(dry_run, bool):
        raise TypeError("dry_run 必须是布尔值")

    if routing.mode is DataRouteMode.LEGACY:
        return None
    if routing.mode is DataRouteMode.PROVIDER_PRIMARY:
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.STAGE_NOT_ENABLED,
            "当前阶段尚未启用 provider_primary",
        )

    quote = parse_tencent_quote_shadow_settings(config)
    if not quote.enabled:
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.SHADOW_NOT_ENABLED,
            "provider_shadow 要求显式启用腾讯 Shadow",
        )
    if not allow_provider_shadow:
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.CLI_GATE_REQUIRED,
            "provider_shadow 要求显式 CLI 安全开关",
        )
    if dry_run:
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.DRY_RUN_REJECTED,
            "dry-run 不执行 Provider Shadow",
        )
    if routing.provider_shadow_database_path is None:
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.DATABASE_PATH_REQUIRED,
            "provider_shadow 要求显式独立数据库路径",
        )
    if routing.max_call_budget != 1:
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.CALL_BUDGET_INVALID,
            "Tencent Provider Shadow 调用预算必须为 1",
        )
    if any(
        provider_id != TENCENT_QUOTE_DESCRIPTOR.provider_id
        for provider_id, _ in routing.provider_priorities
    ):
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.CANDIDATES_INVALID,
            "Tencent Provider Shadow 只允许腾讯 Quote 候选",
        )

    database_path = _resolve_config_path(
        config_path,
        routing.provider_shadow_database_path,
    )
    if database_path.exists() and database_path.is_dir():
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.DATABASE_PATH_INVALID,
            "Provider Shadow 数据库路径不得是目录",
        )
    if database_path == _formal_database_path(config, config_path):
        raise ProviderShadowGateError(
            ProviderShadowGateErrorCode.DATABASE_PATH_CONFLICT,
            "Provider Shadow 数据库必须与正式数据库隔离",
        )
    return TencentProviderShadowGate(routing, quote, database_path)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not is_timezone_aware(value):
        raise ValueError("Shadow clock 必须返回 timezone-aware datetime")
    return value


def _input_issue(
    *,
    now: datetime,
    code: str,
    message: str,
) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.WARNING,
        category=IssueCategory.VALIDATION,
        provider=TENCENT_QUOTE_DESCRIPTOR.provider_id,
        operation=TENCENT_SHADOW_OPERATION,
        message=message,
        retryable=False,
        occurred_at=now,
        code=code,
    )


@dataclass(frozen=True, slots=True)
class TencentShadowInput:
    securities: tuple[Security, ...]
    issues: tuple[DataIssue, ...]


def select_tencent_shadow_securities(
    watchlist: Sequence[object],
    *,
    max_symbols: int,
    now: datetime,
) -> TencentShadowInput:
    """只选合法 CN 证券，稳定去重；不依据名称猜测交易所。"""
    if not isinstance(watchlist, Sequence) or isinstance(
        watchlist, (str, bytes)
    ):
        raise TypeError("watchlist 必须是序列")
    if not isinstance(max_symbols, int) or isinstance(max_symbols, bool):
        raise TypeError("max_symbols 必须是整数")
    if max_symbols <= 0:
        raise ValueError("max_symbols 必须为正")
    if not is_timezone_aware(now):
        raise ValueError("now 必须是 timezone-aware datetime")

    selected: list[Security] = []
    issues: list[DataIssue] = []
    seen: set[str] = set()
    effective_limit = min(max_symbols, _MAX_SINGLE_REQUEST_SYMBOLS)
    for raw in watchlist:
        if not isinstance(raw, dict):
            continue
        market = raw.get("market")
        if not isinstance(market, str) or market.strip().lower() != "cn":
            continue
        raw_symbol = raw.get("symbol")
        symbol = "" if raw_symbol is None else str(raw_symbol).strip()
        raw_name = raw.get("name")
        name = "" if raw_name is None else str(raw_name).strip()
        try:
            security = Security(
                market="cn",
                symbol=symbol,
                name=name or symbol,
            )
            to_tencent_symbol(security)
        except (TypeError, ValueError, ProviderValidationError):
            issues.append(
                _input_issue(
                    now=now,
                    code="unsupported_shadow_security",
                    message="A CN watchlist security was not eligible for Tencent Shadow",
                )
            )
            continue
        if security.symbol in seen:
            issues.append(
                _input_issue(
                    now=now,
                    code="duplicate_shadow_security",
                    message="A duplicate CN watchlist security was ignored",
                )
            )
            continue
        seen.add(security.symbol)
        selected.append(security)
        if len(selected) >= effective_limit:
            break
    return TencentShadowInput(tuple(selected), tuple(issues))


def _missing_issue(symbol: str, *, now: datetime) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.WARNING,
        category=IssueCategory.MISSING_DATA,
        provider=TENCENT_QUOTE_DESCRIPTOR.provider_id,
        operation=TENCENT_SHADOW_OPERATION,
        message="A requested Tencent quote was missing",
        retryable=False,
        occurred_at=now,
        code="missing_requested_symbol",
    )


class TencentQuoteInvoker:
    """单次调用已注入 Provider，并补齐缺失证券的标准 Issue。"""

    def __init__(
        self,
        provider: object,
        securities: tuple[Security, ...],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if provider is None:
            raise ValueError("provider 不能为空")
        self._provider = provider
        self._securities = securities
        self._clock = clock

    def __call__(
        self,
        registration: ProviderRegistration,
    ) -> ProviderResult[MarketSnapshot]:
        if registration.descriptor != TENCENT_QUOTE_DESCRIPTOR:
            raise ProviderValidationError(
                provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                operation=TENCENT_SHADOW_OPERATION,
                safe_message="Tencent Shadow registration was invalid",
                code="invalid_shadow_registration",
            )
        fetch_quotes = getattr(self._provider, "fetch_quotes", None)
        if not callable(fetch_quotes):
            raise ProviderValidationError(
                provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                operation=TENCENT_SHADOW_OPERATION,
                safe_message="Tencent Shadow provider was invalid",
                code="invalid_shadow_provider",
            )
        result = fetch_quotes(self._securities)
        if not isinstance(result, ProviderResult):
            return result
        returned = {
            item.symbol
            for item in result.items
            if isinstance(item, MarketSnapshot)
        }
        missing = tuple(
            _missing_issue(security.symbol, now=_aware_now(self._clock))
            for security in self._securities
            if security.symbol not in returned
        )
        if not missing:
            return result
        return ProviderResult(
            provider=result.provider,
            items=result.items,
            issues=result.issues + missing,
        )


class LazyTencentQuoteInvoker:
    """仅在 Circuit allow/probe 后由 Router 首次进入时构造 Provider。"""

    def __init__(
        self,
        factory: Callable[[TencentQuoteShadowSettings], object],
        settings: TencentQuoteShadowSettings,
        securities: tuple[Security, ...],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._securities = securities
        self._clock = clock

    def __call__(
        self,
        registration: ProviderRegistration,
    ) -> ProviderResult[MarketSnapshot]:
        provider = self._factory(self._settings)
        return TencentQuoteInvoker(
            provider,
            self._securities,
            clock=self._clock,
        )(registration)


class TencentQuoteResultEvaluator:
    """纯 evaluator：不转换 PriceWindow，也不请求第二来源。"""

    def __init__(self, requested: tuple[Security, ...]) -> None:
        self._requested_symbols = tuple(item.symbol for item in requested)

    def __call__(
        self,
        result: ProviderResult[MarketSnapshot],
    ) -> ResultEvaluation:
        symbols: list[str] = []
        for item in result.items:
            if not isinstance(item, MarketSnapshot):
                raise TypeError("Tencent Shadow 只接受 MarketSnapshot")
            if item.symbol not in self._requested_symbols:
                raise ValueError("Tencent Shadow 返回了请求外证券")
            symbols.append(item.symbol)
        if len(symbols) != len(set(symbols)):
            raise ValueError("Tencent Shadow 返回了重复证券")
        if not result.items:
            return ResultEvaluation(RouteTerminalStatus.EMPTY)
        has_blocking_issue = any(
            issue.severity is IssueSeverity.ERROR for issue in result.issues
        )
        if (
            len(set(symbols)) < len(self._requested_symbols)
            or has_blocking_issue
        ):
            return ResultEvaluation(
                RouteTerminalStatus.PARTIAL,
                FallbackDecision.STOP,
            )
        return ResultEvaluation(RouteTerminalStatus.SUCCESS)


def build_tencent_shadow_registry(*, priority: int = 0) -> ProviderRegistry:
    return ProviderRegistry(
        (
            ProviderRegistration(
                descriptor=TENCENT_QUOTE_DESCRIPTOR,
                capability=ProviderCapability.QUOTE,
                market="cn",
                priority=priority,
                enabled=True,
                runtime_stage=ProviderRuntimeStage.SHADOW_ELIGIBLE,
            ),
        )
    )


def build_tencent_shadow_policy() -> RoutePolicy:
    return RoutePolicy(
        market="cn",
        capability=ProviderCapability.QUOTE,
        candidate_provider_ids=(TENCENT_QUOTE_DESCRIPTOR.provider_id,),
        allow_legacy_fallback=False,
        max_call_budget=1,
        mode=DataRouteMode.PROVIDER_SHADOW,
        fallback_on_empty=False,
    )


class _CircuitStore(Protocol):
    def preflight(self, key, policy, *, now):
        ...

    def record_success(self, key, *, expected_version, now):
        ...

    def record_failure(
        self,
        key,
        policy,
        classification,
        *,
        expected_version,
        now,
        origin=CircuitOutcomeOrigin.PROVIDER,
    ):
        ...


class TencentProviderShadowPersistence:
    """独立 Shadow SQLite 适配器；构造阶段不连接数据库。"""

    def __init__(self, database: object) -> None:
        if database is None:
            raise ValueError("database 不能为空")
        self.database = database

    def start_run(
        self,
        run_id: str,
        started_at: datetime,
        *,
        config_hash: str | None,
    ) -> None:
        from daily_report_agent.storage.repositories import PipelineRunRepository

        with self.database.transaction() as connection:
            PipelineRunRepository(connection).start_run(
                run_id,
                started_at,
                dry_run=False,
                config_hash=config_hash,
            )

    def finish_run(
        self,
        run_id: str,
        status: str,
        finished_at: datetime,
        *,
        created_snapshots: int,
        error_summary: str | None,
    ) -> None:
        from daily_report_agent.storage.repositories import PipelineRunRepository

        with self.database.transaction() as connection:
            PipelineRunRepository(connection).finish_run(
                run_id,
                status,
                finished_at,
                created_snapshots=created_snapshots,
                error_summary=error_summary,
            )

    def start_call(
        self,
        run_id: str,
        started_at: datetime,
        *,
        request_fingerprint: str,
    ) -> int:
        from daily_report_agent.storage.repositories import ProviderCallRepository

        with self.database.transaction() as connection:
            return ProviderCallRepository(connection).start_call(
                run_id,
                TENCENT_QUOTE_DESCRIPTOR.provider_id,
                TENCENT_SHADOW_OPERATION,
                started_at,
                request_fingerprint=request_fingerprint,
            )

    def finish_call(
        self,
        call_id: int,
        status: str,
        finished_at: datetime,
        *,
        duration_ms: int,
        item_count: int,
        error_code: str | None = None,
        error_category: str | None = None,
    ) -> None:
        from daily_report_agent.storage.repositories import ProviderCallRepository

        with self.database.transaction() as connection:
            ProviderCallRepository(connection).finish_call(
                call_id,
                status,
                finished_at,
                duration_ms=duration_ms,
                item_count=item_count,
                retry_count=0,
                error_severity="error" if status == "failed" else None,
                error_category=error_category,
                error_code=error_code,
                error_message=(
                    "Tencent Provider Shadow failed"
                    if status == "failed"
                    else None
                ),
            )

    def save_snapshot(
        self,
        security: Security,
        snapshot: MarketSnapshot,
        *,
        fetched_at: datetime,
    ) -> bool:
        from daily_report_agent.storage.repositories import (
            MarketSnapshotRepository,
            SecurityRepository,
        )

        if (
            snapshot.source != TENCENT_QUOTE_DESCRIPTOR.provider_id
            or snapshot.symbol != security.symbol
        ):
            raise ValueError("Shadow snapshot identity is invalid")
        with self.database.transaction() as connection:
            security_id = SecurityRepository(connection).upsert_security(security)
            _, inserted = MarketSnapshotRepository(
                connection
            ).insert_or_get_snapshot(security_id, snapshot, fetched_at)
        return inserted


@dataclass(frozen=True, slots=True)
class TencentProviderShadowResult:
    status: RouteTerminalStatus
    requested_count: int
    item_count: int
    issue_count: int
    created_snapshot_count: int
    call_budget_used: int
    provider_call_id: int | None
    safe_error_code: str | None = None
    route_result: RouteResult[MarketSnapshot] | None = None


def _fingerprint(securities: tuple[Security, ...]) -> str:
    canonical = "\n".join(
        f"{item.market}:{item.symbol}" for item in securities
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _duration_ms(started: float, monotonic: Callable[[], float]) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _route_error_origin(route_result: RouteResult[MarketSnapshot]):
    attempt = route_result.attempts[-1]
    if attempt.error_code == RouteErrorCode.EVALUATOR_FAILED.value:
        return CircuitOutcomeOrigin.EVALUATOR
    if attempt.error_code in {
        RouteErrorCode.INVALID_PROVIDER_RESULT.value,
        RouteErrorCode.INVALID_EVALUATION.value,
    }:
        return CircuitOutcomeOrigin.CALLER
    retry = attempt.retry_attempts[-1]
    if retry.error_class is ProviderErrorClass.VALIDATION:
        return CircuitOutcomeOrigin.INPUT
    return CircuitOutcomeOrigin.PROVIDER


class TencentProviderShadowOrchestrator:
    """Circuit preflight → 单候选 Router → 持久化；不接触 legacy 输出。"""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        invoker: ProviderInvoker[MarketSnapshot],
        evaluator: ResultEvaluator[MarketSnapshot],
        circuit_store: _CircuitStore,
        persistence: TencentProviderShadowPersistence,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        metric_sink: ProviderMetricEmitter = log_provider_metric,
        circuit_policy: CircuitBreakerPolicy = CircuitBreakerPolicy(),
    ) -> None:
        self.registry = registry
        self.invoker = invoker
        self.evaluator = evaluator
        self.circuit_store = circuit_store
        self.persistence = persistence
        self.clock = clock
        self.monotonic = monotonic
        self.metric_sink = metric_sink
        self.circuit_policy = circuit_policy

    def _metric(
        self,
        status: ProviderMetricStatus,
        *,
        duration_ms: int,
        item_count: int = 0,
        issue_count: int = 0,
        error_code: str | None = None,
    ) -> None:
        emit_provider_metric_safely(
            ProviderMetricEvent(
                provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                operation=TENCENT_SHADOW_OPERATION,
                status=status,
                duration_ms=duration_ms,
                item_count=item_count,
                issue_count=issue_count,
                retry_count=0,
                error_code=error_code,
            ),
            self.metric_sink,
        )

    def execute(
        self,
        shadow_input: TencentShadowInput,
        *,
        config_hash: str | None = None,
        run_id: str | None = None,
    ) -> TencentProviderShadowResult:
        started_at = _aware_now(self.clock)
        started_tick = self.monotonic()
        selected_run_id = run_id or f"provider-shadow-{uuid4()}"
        try:
            self.persistence.start_run(
                selected_run_id,
                started_at,
                config_hash=config_hash,
            )
        except Exception:
            self._metric(
                ProviderMetricStatus.SKIPPED,
                duration_ms=_duration_ms(started_tick, self.monotonic),
                error_code="shadow_storage_failed",
            )
            return TencentProviderShadowResult(
                RouteTerminalStatus.SKIPPED,
                len(shadow_input.securities),
                0,
                len(shadow_input.issues),
                0,
                0,
                None,
                "shadow_storage_failed",
            )

        if not shadow_input.securities:
            result = self._persist_skip(
                selected_run_id,
                started_at,
                started_tick,
                shadow_input,
                error_code="no_eligible_security",
            )
            return result

        try:
            preflight = self.circuit_store.preflight(
                TENCENT_SHADOW_KEY,
                self.circuit_policy,
                now=_aware_now(self.clock),
            )
        except Exception:
            return self._persist_skip(
                selected_run_id,
                started_at,
                started_tick,
                shadow_input,
                error_code="circuit_storage_failed",
            )
        if preflight.transition.decision is CircuitDecision.SKIP:
            return self._persist_skip(
                selected_run_id,
                started_at,
                started_tick,
                shadow_input,
                error_code="circuit_open",
            )

        try:
            call_id = self.persistence.start_call(
                selected_run_id,
                started_at,
                request_fingerprint=_fingerprint(shadow_input.securities),
            )
        except Exception:
            self._finish_run_safely(
                selected_run_id,
                "partial",
                created_snapshots=0,
                error_summary="shadow_storage_failed",
            )
            self._metric(
                ProviderMetricStatus.SKIPPED,
                duration_ms=_duration_ms(started_tick, self.monotonic),
                error_code="shadow_storage_failed",
            )
            return TencentProviderShadowResult(
                RouteTerminalStatus.SKIPPED,
                len(shadow_input.securities),
                0,
                len(shadow_input.issues),
                0,
                0,
                None,
                "shadow_storage_failed",
            )

        router = ProviderRouter(
            registry=self.registry,
            policy=build_tencent_shadow_policy(),
            invoker=self.invoker,
            evaluator=self.evaluator,
            retry_policy=RetryPolicy(
                max_attempts_per_provider=1,
                allow_fallback_after_retry=False,
            ),
        )
        route_result = router.execute()
        duration = _duration_ms(started_tick, self.monotonic)

        if route_result.provider_result is None:
            if (
                not route_result.attempts
                or not route_result.attempts[-1].retry_attempts
            ):
                safe_code = (
                    route_result.error_code.value
                    if route_result.error_code is not None
                    else RouteErrorCode.NO_ELIGIBLE_PROVIDER.value
                )
                try:
                    from daily_report_agent.providers.routing import (
                        ErrorClassification,
                        RetryErrorCode,
                    )

                    self.circuit_store.record_failure(
                        TENCENT_SHADOW_KEY,
                        self.circuit_policy,
                        ErrorClassification(
                            RetryErrorCode.INVALID_PROVIDER_RESULT,
                            ProviderErrorClass.PROTOCOL,
                        ),
                        expected_version=preflight.version,
                        now=_aware_now(self.clock),
                        origin=CircuitOutcomeOrigin.CALLER,
                    )
                except Exception:
                    safe_code = "circuit_storage_failed"
                self._finish_call_safely(
                    call_id,
                    "skipped",
                    duration=duration,
                    item_count=0,
                    error_code=safe_code,
                )
                self._finish_run_safely(
                    selected_run_id,
                    "partial",
                    created_snapshots=0,
                    error_summary=safe_code,
                )
                self._metric(
                    ProviderMetricStatus.SKIPPED,
                    duration_ms=duration,
                    error_code=safe_code,
                )
                return TencentProviderShadowResult(
                    RouteTerminalStatus.SKIPPED,
                    len(shadow_input.securities),
                    0,
                    len(shadow_input.issues),
                    0,
                    route_result.call_budget_used,
                    call_id,
                    safe_code,
                    route_result,
                )
            classification = _classification_from_route(route_result)
            try:
                self.circuit_store.record_failure(
                    TENCENT_SHADOW_KEY,
                    self.circuit_policy,
                    classification,
                    expected_version=preflight.version,
                    now=_aware_now(self.clock),
                    origin=_route_error_origin(route_result),
                )
            except Exception:
                safe_code = "circuit_storage_failed"
            else:
                safe_code = classification.error_code.value
            self._finish_call_safely(
                call_id,
                "failed",
                duration=duration,
                item_count=0,
                error_code=safe_code,
                error_category=_error_category(classification.error_class),
            )
            self._finish_run_safely(
                selected_run_id,
                "partial",
                created_snapshots=0,
                error_summary=safe_code,
            )
            self._metric(
                ProviderMetricStatus.FAILED,
                duration_ms=duration,
                issue_count=len(shadow_input.issues) + 1,
                error_code=safe_code,
            )
            return TencentProviderShadowResult(
                RouteTerminalStatus.FAILED,
                len(shadow_input.securities),
                0,
                len(shadow_input.issues) + 1,
                0,
                route_result.call_budget_used,
                call_id,
                safe_code,
                route_result,
            )

        try:
            self.circuit_store.record_success(
                TENCENT_SHADOW_KEY,
                expected_version=preflight.version,
                now=_aware_now(self.clock),
            )
        except Exception:
            self._finish_call_safely(
                call_id,
                "failed",
                duration=duration,
                item_count=0,
                error_code="circuit_storage_failed",
            )
            self._finish_run_safely(
                selected_run_id,
                "partial",
                created_snapshots=0,
                error_summary="circuit_storage_failed",
            )
            self._metric(
                ProviderMetricStatus.FAILED,
                duration_ms=duration,
                error_code="circuit_storage_failed",
            )
            return TencentProviderShadowResult(
                RouteTerminalStatus.FAILED,
                len(shadow_input.securities),
                0,
                len(shadow_input.issues),
                0,
                route_result.call_budget_used,
                call_id,
                "circuit_storage_failed",
                route_result,
            )

        provider_result = route_result.provider_result
        by_symbol = {
            security.symbol: security for security in shadow_input.securities
        }
        created = 0
        storage_failures = 0
        for snapshot in provider_result.items:
            try:
                security = by_symbol[snapshot.symbol]
                created += int(
                    self.persistence.save_snapshot(
                        security,
                        snapshot,
                        fetched_at=started_at,
                    )
                )
            except Exception:
                storage_failures += 1

        item_count = len(provider_result.items)
        issue_count = (
            len(shadow_input.issues)
            + len(provider_result.issues)
            + storage_failures
        )
        terminal = route_result.terminal_status or RouteTerminalStatus.FAILED
        if storage_failures or shadow_input.issues:
            terminal = RouteTerminalStatus.PARTIAL
        call_status = "empty" if item_count == 0 else "success"
        call_finished = self._finish_call_safely(
            call_id,
            call_status,
            duration=duration,
            item_count=item_count,
        )
        if not call_finished:
            terminal = RouteTerminalStatus.PARTIAL
        run_status = (
            "success"
            if terminal in {
                RouteTerminalStatus.SUCCESS,
                RouteTerminalStatus.EMPTY,
            }
            and not shadow_input.issues
            else "partial"
        )
        run_finished = self._finish_run_safely(
            selected_run_id,
            run_status,
            created_snapshots=created,
            error_summary=(
                None if run_status == "success" else "provider_shadow_partial"
            ),
        )
        if not run_finished:
            terminal = RouteTerminalStatus.PARTIAL
        metric_status = ProviderMetricStatus(terminal.value)
        self._metric(
            metric_status,
            duration_ms=duration,
            item_count=item_count,
            issue_count=issue_count,
        )
        return TencentProviderShadowResult(
            terminal,
            len(shadow_input.securities),
            item_count,
            issue_count,
            created,
            route_result.call_budget_used,
            call_id,
            None if call_finished and run_finished else "shadow_storage_failed",
            route_result,
        )

    def _persist_skip(
        self,
        run_id: str,
        started_at: datetime,
        started_tick: float,
        shadow_input: TencentShadowInput,
        *,
        error_code: str,
    ) -> TencentProviderShadowResult:
        call_id = None
        try:
            call_id = self.persistence.start_call(
                run_id,
                started_at,
                request_fingerprint=_fingerprint(shadow_input.securities),
            )
            self.persistence.finish_call(
                call_id,
                "skipped",
                _aware_now(self.clock),
                duration_ms=_duration_ms(started_tick, self.monotonic),
                item_count=0,
                error_code=error_code,
            )
        except Exception:
            error_code = "shadow_storage_failed"
        self._finish_run_safely(
            run_id,
            "partial",
            created_snapshots=0,
            error_summary=error_code,
        )
        self._metric(
            ProviderMetricStatus.SKIPPED,
            duration_ms=_duration_ms(started_tick, self.monotonic),
            issue_count=len(shadow_input.issues),
            error_code=error_code,
        )
        return TencentProviderShadowResult(
            RouteTerminalStatus.SKIPPED,
            len(shadow_input.securities),
            0,
            len(shadow_input.issues),
            0,
            0,
            call_id,
            error_code,
        )

    def _finish_call_safely(
        self,
        call_id: int,
        status: str,
        *,
        duration: int,
        item_count: int,
        error_code: str | None = None,
        error_category: str | None = None,
    ) -> bool:
        try:
            self.persistence.finish_call(
                call_id,
                status,
                _aware_now(self.clock),
                duration_ms=duration,
                item_count=item_count,
                error_code=error_code,
                error_category=error_category,
            )
        except Exception:
            return False
        return True

    def _finish_run_safely(
        self,
        run_id: str,
        status: str,
        *,
        created_snapshots: int,
        error_summary: str | None,
    ) -> bool:
        try:
            self.persistence.finish_run(
                run_id,
                status,
                _aware_now(self.clock),
                created_snapshots=created_snapshots,
                error_summary=error_summary,
            )
        except Exception:
            return False
        return True


def _classification_from_route(route_result: RouteResult[MarketSnapshot]):
    retry = route_result.attempts[-1].retry_attempts[-1]
    from daily_report_agent.providers.routing import ErrorClassification

    return ErrorClassification(retry.error_code, retry.error_class)


def _error_category(error_class: ProviderErrorClass) -> str:
    return {
        ProviderErrorClass.TRANSIENT: "network",
        ProviderErrorClass.RATE_LIMITED: "rate_limit",
        ProviderErrorClass.UNAVAILABLE: "provider_unavailable",
        ProviderErrorClass.BLOCKED: "provider_unavailable",
        ProviderErrorClass.AUTHENTICATION: "auth",
        ProviderErrorClass.VALIDATION: "validation",
        ProviderErrorClass.PROTOCOL: "parse",
        ProviderErrorClass.PERMANENT: "unknown",
        ProviderErrorClass.UNKNOWN: "unknown",
    }[error_class]


def _default_provider_factory(
    settings: TencentQuoteShadowSettings,
) -> object:
    """所有模式、CLI、dry-run、路径及 Circuit 门禁之后才允许调用。"""
    from daily_report_agent.providers.tencent.online_transport import (
        TencentOnlineQuoteTransport,
    )
    from daily_report_agent.providers.tencent.quote import TencentQuoteProvider

    return TencentQuoteProvider(
        TencentOnlineQuoteTransport(),
        timeout_seconds=min(settings.timeout_seconds, 10.0),
        batch_size=_MAX_SINGLE_REQUEST_SYMBOLS,
    )


def run_tencent_provider_shadow_from_config(
    *,
    gate: TencentProviderShadowGate,
    config_path: str,
    watchlist: Sequence[object],
    provider_factory: Callable[
        [TencentQuoteShadowSettings], object
    ] = _default_provider_factory,
    registry: ProviderRegistry | None = None,
    invoker: ProviderInvoker[MarketSnapshot] | None = None,
    evaluator: ResultEvaluator[MarketSnapshot] | None = None,
    database: object | None = None,
    circuit_store: _CircuitStore | None = None,
    clock: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    metric_sink: ProviderMetricEmitter = log_provider_metric,
    circuit_policy: CircuitBreakerPolicy = CircuitBreakerPolicy(),
) -> TencentProviderShadowResult:
    """门禁通过后的装配入口；依赖均可替换为纯离线 Fake。"""
    now = _aware_now(clock)
    shadow_input = select_tencent_shadow_securities(
        watchlist,
        max_symbols=gate.quote.max_symbols,
        now=now,
    )
    selected_database = database
    try:
        if selected_database is None:
            from daily_report_agent.storage.database import Database

            gate.database_path.parent.mkdir(parents=True, exist_ok=True)
            selected_database = Database(gate.database_path)
            selected_database.initialize()
        if circuit_store is None:
            from daily_report_agent.storage.circuit_breaker import (
                SQLiteCircuitBreakerStore,
            )

            circuit_store = SQLiteCircuitBreakerStore(selected_database)
    except Exception:
        return TencentProviderShadowResult(
            RouteTerminalStatus.SKIPPED,
            len(shadow_input.securities),
            0,
            len(shadow_input.issues),
            0,
            0,
            None,
            "shadow_storage_failed",
        )

    priority = dict(gate.routing.provider_priorities).get(
        TENCENT_QUOTE_DESCRIPTOR.provider_id,
        0,
    )
    selected_registry = registry or build_tencent_shadow_registry(
        priority=priority
    )
    selected_invoker = invoker or LazyTencentQuoteInvoker(
        provider_factory,
        gate.quote,
        shadow_input.securities,
        clock=clock,
    )
    selected_evaluator = evaluator or TencentQuoteResultEvaluator(
        shadow_input.securities
    )
    config_hash = hashlib.sha256(
        Path(config_path).read_bytes()
    ).hexdigest()
    return TencentProviderShadowOrchestrator(
        registry=selected_registry,
        invoker=selected_invoker,
        evaluator=selected_evaluator,
        circuit_store=circuit_store,
        persistence=TencentProviderShadowPersistence(selected_database),
        clock=clock,
        monotonic=monotonic,
        metric_sink=metric_sink,
        circuit_policy=circuit_policy,
    ).execute(shadow_input, config_hash=config_hash)
