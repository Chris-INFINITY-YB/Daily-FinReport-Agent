"""默认关闭的腾讯行情 Shadow 旁路；不参与日报金融分析。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from daily_report_agent.config import (
    TencentQuoteShadowSettings,
    parse_tencent_quote_shadow_settings,
)
from daily_report_agent.models.issues import IssueSeverity
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import is_timezone_aware
from daily_report_agent.models.security import Security
from daily_report_agent.providers.telemetry import (
    ProviderMetricEmitter,
    ProviderMetricEvent,
    ProviderMetricStatus,
    emit_provider_metric_safely,
    log_provider_metric,
)
from .context import RunContext

if TYPE_CHECKING:
    from daily_report_agent.providers.base import QuoteProvider


class _RuntimeQuoteProvider(Protocol):
    def fetch_quotes(self, securities: tuple[Security, ...]):
        ...


class _QuoteTextTransport(Protocol):
    def fetch_quote_text(
        self,
        symbols: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> str:
        ...


class SequentialTencentQuoteTransport:
    """按底层安全上限顺序分片；不重试、不并发。"""

    def __init__(self, transport: _QuoteTextTransport, *, max_symbols_per_request: int) -> None:
        if max_symbols_per_request <= 0:
            raise ValueError("max_symbols_per_request must be positive")
        self._transport = transport
        self._max_symbols_per_request = max_symbols_per_request

    def fetch_quote_text(
        self,
        symbols: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> str:
        responses = []
        for start in range(0, len(symbols), self._max_symbols_per_request):
            responses.append(
                self._transport.fetch_quote_text(
                    symbols[start : start + self._max_symbols_per_request],
                    timeout_seconds=timeout_seconds,
                )
            )
        return "\n".join(responses)


TENCENT_PROVIDER_ID = "tencent-finance"
TENCENT_SHADOW_OPERATION = "quote_shadow"


@dataclass(frozen=True, slots=True)
class TencentQuoteShadowResult:
    executed: bool
    status: str
    requested_count: int
    received_count: int
    created_snapshot_count: int
    issue_count: int
    provider_call_id: int | None
    duration_ms: int | None
    safe_message: str | None = None


class TencentQuoteShadowStore(Protocol):
    def start_call(
        self,
        *,
        run_id: str,
        started_at: datetime,
        request_fingerprint: str,
    ) -> int:
        ...

    def save_snapshot(
        self,
        *,
        security: Security,
        snapshot: MarketSnapshot,
        fetched_at: datetime,
    ) -> bool:
        ...

    def finish_call(
        self,
        call_id: int,
        status: str,
        finished_at: datetime,
        *,
        duration_ms: int,
        item_count: int,
        error_severity: str | None = None,
        error_category: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        ...


class SQLiteTencentQuoteShadowStore:
    """使用现有 Repository 的最小事务适配器，不创建或迁移数据库。"""

    def __init__(self, run_context: RunContext) -> None:
        if not run_context.storage_active:
            raise ValueError("Shadow store requires an active storage context")
        self._database = run_context.database

    def start_call(
        self,
        *,
        run_id: str,
        started_at: datetime,
        request_fingerprint: str,
    ) -> int:
        from daily_report_agent.storage.repositories import ProviderCallRepository

        with self._database.transaction() as connection:
            return ProviderCallRepository(connection).start_call(
                run_id,
                TENCENT_PROVIDER_ID,
                TENCENT_SHADOW_OPERATION,
                started_at,
                security_id=None,
                request_fingerprint=request_fingerprint,
            )

    def save_snapshot(
        self,
        *,
        security: Security,
        snapshot: MarketSnapshot,
        fetched_at: datetime,
    ) -> bool:
        from daily_report_agent.storage.repositories import (
            MarketSnapshotRepository,
            SecurityRepository,
        )

        if snapshot.source != TENCENT_PROVIDER_ID or snapshot.symbol != security.symbol:
            raise ValueError("Shadow snapshot identity is invalid")
        with self._database.transaction() as connection:
            security_id = SecurityRepository(connection).upsert_security(security)
            _, inserted = MarketSnapshotRepository(connection).insert_or_get_snapshot(
                security_id,
                snapshot,
                fetched_at,
            )
        return inserted

    def finish_call(
        self,
        call_id: int,
        status: str,
        finished_at: datetime,
        *,
        duration_ms: int,
        item_count: int,
        error_severity: str | None = None,
        error_category: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        from daily_report_agent.storage.repositories import ProviderCallRepository

        with self._database.transaction() as connection:
            ProviderCallRepository(connection).finish_call(
                call_id,
                status,
                finished_at,
                duration_ms=duration_ms,
                item_count=item_count,
                retry_count=0,
                error_severity=error_severity,
                error_category=error_category,
                error_code=error_code,
                error_message=error_message,
            )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_time(value: datetime) -> datetime:
    if not is_timezone_aware(value):
        raise ValueError("Shadow clock must return a timezone-aware datetime")
    return value


def build_request_fingerprint(securities: tuple[Security, ...]) -> str:
    canonical = "\n".join(sorted(f"{item.market}:{item.symbol}" for item in securities))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_shadow_securities(
    watchlist: Sequence[object],
    *,
    max_symbols: int,
) -> tuple[Security, ...]:
    result: list[Security] = []
    for raw in watchlist:
        if not isinstance(raw, dict) or str(raw.get("market", "")).strip().lower() != "cn":
            continue
        raw_symbol = raw.get("symbol")
        raw_name = raw.get("name")
        symbol = "" if raw_symbol is None else str(raw_symbol).strip()
        name = "" if raw_name is None else str(raw_name).strip()
        name = name or symbol
        result.append(Security(market="cn", symbol=symbol, name=name))
        if len(result) >= max_symbols:
            break
    return tuple(result)


def _default_provider_factory(
    settings: TencentQuoteShadowSettings,
) -> _RuntimeQuoteProvider:
    # 必须位于完整门禁和 ProviderCall 建立之后，关闭路径不加载在线 Transport。
    from daily_report_agent.providers.tencent.online_transport import (
        MAX_ONLINE_SYMBOLS,
        TencentOnlineQuoteTransport,
    )
    from daily_report_agent.providers.tencent.quote import TencentQuoteProvider

    return TencentQuoteProvider(
        SequentialTencentQuoteTransport(
            TencentOnlineQuoteTransport(),
            max_symbols_per_request=MAX_ONLINE_SYMBOLS,
        ),
        timeout_seconds=settings.timeout_seconds,
        batch_size=settings.batch_size,
    )


def _warn(message: str) -> None:
    print(f"[腾讯 Shadow 警告] {message}")


def _duration_ms(started: float, monotonic: Callable[[], float]) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _emit_metric(
    *,
    status: ProviderMetricStatus,
    duration_ms: int,
    item_count: int = 0,
    issue_count: int = 0,
    error_code: str | None = None,
    emitter: ProviderMetricEmitter,
) -> None:
    emit_provider_metric_safely(
        ProviderMetricEvent(
            provider_id=TENCENT_PROVIDER_ID,
            operation=TENCENT_SHADOW_OPERATION,
            status=status,
            duration_ms=duration_ms,
            item_count=item_count,
            issue_count=issue_count,
            retry_count=0,
            error_code=error_code,
        ),
        emitter,
    )


def _finish_safely(
    store: TencentQuoteShadowStore,
    call_id: int,
    status: str,
    *,
    clock: Callable[[], datetime],
    started_tick: float,
    monotonic: Callable[[], float],
    item_count: int,
    error_severity: str | None = None,
    error_category: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> tuple[int, bool]:
    duration = _duration_ms(started_tick, monotonic)
    try:
        store.finish_call(
            call_id,
            status,
            _validate_time(clock()),
            duration_ms=duration,
            item_count=item_count,
            error_severity=error_severity,
            error_category=error_category,
            error_code=error_code,
            error_message=error_message,
        )
    except Exception:
        _warn("ProviderCall 结束记录失败，正式日报将继续。")
        return duration, False
    return duration, True


def run_tencent_quote_shadow(
    *,
    securities: tuple[Security, ...],
    run_context: RunContext,
    settings: TencentQuoteShadowSettings,
    store: TencentQuoteShadowStore,
    provider_factory: Callable[
        [TencentQuoteShadowSettings], _RuntimeQuoteProvider
    ] = _default_provider_factory,
    clock: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    metric_emitter: ProviderMetricEmitter = log_provider_metric,
) -> TencentQuoteShadowResult:
    """执行一次可追踪的逻辑 Provider 调用；所有失败均与正式日报隔离。"""
    requested_count = len(securities)
    started_at = _validate_time(clock())
    started_tick = monotonic()
    try:
        call_id = store.start_call(
            run_id=run_context.pipeline_run_id,
            started_at=started_at,
            request_fingerprint=build_request_fingerprint(securities),
        )
    except Exception:
        _warn("ProviderCall 无法建立，本次未执行腾讯网络请求。")
        _emit_metric(
            status=ProviderMetricStatus.SKIPPED,
            duration_ms=_duration_ms(started_tick, monotonic),
            error_code="provider_call_start_failed",
            emitter=metric_emitter,
        )
        return TencentQuoteShadowResult(
            False, "skipped", requested_count, 0, 0, 0, None, None,
            "provider_call_start_failed",
        )

    try:
        # Provider 错误类也延迟导入，避免 providers 包在关闭路径加载腾讯在线模块。
        from daily_report_agent.providers.errors import (
            ProviderError,
            provider_error_to_issue,
        )

        provider = provider_factory(settings)
        provider_result = provider.fetch_quotes(securities)
    except ProviderError as error:
        issue = provider_error_to_issue(error, occurred_at=started_at)
        duration, _ = _finish_safely(
            store,
            call_id,
            "failed",
            clock=clock,
            started_tick=started_tick,
            monotonic=monotonic,
            item_count=0,
            error_severity=issue.severity.value,
            error_category=issue.category.value,
            error_code=issue.code,
            error_message=issue.message,
        )
        print(
            "腾讯行情 Shadow 失败："
            f"category={issue.category.value} code={issue.code or 'none'}"
        )
        result = TencentQuoteShadowResult(
            True, "failed", requested_count, 0, 0, 1, call_id, duration,
            "provider_failed",
        )
        _emit_metric(
            status=ProviderMetricStatus.FAILED,
            duration_ms=duration,
            issue_count=1,
            error_code=error.code or "provider_error",
            emitter=metric_emitter,
        )
        return result
    except Exception:
        duration, _ = _finish_safely(
            store,
            call_id,
            "failed",
            clock=clock,
            started_tick=started_tick,
            monotonic=monotonic,
            item_count=0,
            error_severity="error",
            error_category="unknown",
            error_code="unexpected_error",
            error_message="Tencent quote shadow failed unexpectedly",
        )
        _warn("Provider 出现未分类失败，正式日报将继续。")
        result = TencentQuoteShadowResult(
            True, "failed", requested_count, 0, 0, 1, call_id, duration,
            "provider_failed",
        )
        _emit_metric(
            status=ProviderMetricStatus.FAILED,
            duration_ms=duration,
            issue_count=1,
            error_code="unexpected_error",
            emitter=metric_emitter,
        )
        return result

    received_count = len(provider_result.items)
    issue_count = len(provider_result.issues)
    created_count = 0
    storage_failures = 0
    securities_by_symbol = {item.symbol: item for item in securities}
    for snapshot in provider_result.items:
        try:
            if not isinstance(snapshot, MarketSnapshot):
                raise TypeError("Shadow only accepts MarketSnapshot")
            security = securities_by_symbol.get(snapshot.symbol)
            if security is None:
                raise ValueError("Snapshot identity is outside the request")
            created_count += int(
                store.save_snapshot(
                    security=security,
                    snapshot=snapshot,
                    fetched_at=started_at,
                )
            )
        except Exception:
            storage_failures += 1
            _warn("单条 Snapshot 保存失败，其他项将继续。")

    issue_count += storage_failures
    provider_status = "success" if received_count else "empty"
    has_error_issue = any(
        issue.severity is IssueSeverity.ERROR for issue in provider_result.issues
    )
    result_status = (
        "empty"
        if received_count == 0
        else "partial"
        if has_error_issue or storage_failures
        else "success"
    )
    duration, finished = _finish_safely(
        store,
        call_id,
        provider_status,
        clock=clock,
        started_tick=started_tick,
        monotonic=monotonic,
        item_count=received_count,
    )
    if not finished and result_status == "success":
        result_status = "partial"

    result = TencentQuoteShadowResult(
        True,
        result_status,
        requested_count,
        received_count,
        created_count,
        issue_count,
        call_id,
        duration,
        None if finished else "provider_call_finish_failed",
    )
    print(
        "腾讯行情 Shadow："
        f"requested={result.requested_count} received={result.received_count} "
        f"created={result.created_snapshot_count} issues={result.issue_count} "
        f"status={result.status} duration_ms={result.duration_ms}"
    )
    _emit_metric(
        status=ProviderMetricStatus(result.status),
        duration_ms=duration,
        item_count=received_count,
        issue_count=issue_count,
        emitter=metric_emitter,
    )
    return result


def maybe_run_tencent_quote_shadow(
    *,
    config: dict,
    watchlist: Sequence[object],
    run_context: RunContext,
    dry_run: bool,
    provider_factory: Callable[
        [TencentQuoteShadowSettings], _RuntimeQuoteProvider
    ] = _default_provider_factory,
    store_factory: Callable[[RunContext], TencentQuoteShadowStore] = SQLiteTencentQuoteShadowStore,
    clock: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    metric_emitter: ProviderMetricEmitter = log_provider_metric,
) -> TencentQuoteShadowResult:
    settings = parse_tencent_quote_shadow_settings(config)
    if dry_run or not settings.enabled:
        return TencentQuoteShadowResult(False, "disabled", 0, 0, 0, 0, None, None)
    if not run_context.storage_active:
        _warn("腾讯行情 Shadow 已启用，但存储未启用，本次已跳过旁路观测。")
        _emit_metric(
            status=ProviderMetricStatus.SKIPPED,
            duration_ms=0,
            error_code="storage_inactive",
            emitter=metric_emitter,
        )
        return TencentQuoteShadowResult(
            False, "skipped", 0, 0, 0, 0, None, None, "storage_inactive"
        )
    securities = build_shadow_securities(
        watchlist,
        max_symbols=settings.max_symbols,
    )
    if not securities:
        _emit_metric(
            status=ProviderMetricStatus.SKIPPED,
            duration_ms=0,
            emitter=metric_emitter,
        )
        return TencentQuoteShadowResult(False, "skipped", 0, 0, 0, 0, None, None)
    try:
        store = store_factory(run_context)
    except Exception:
        _warn("Shadow 存储适配器初始化失败，本次未执行腾讯网络请求。")
        _emit_metric(
            status=ProviderMetricStatus.SKIPPED,
            duration_ms=0,
            error_code="store_initialization_failed",
            emitter=metric_emitter,
        )
        return TencentQuoteShadowResult(
            False, "skipped", len(securities), 0, 0, 0, None, None,
            "store_initialization_failed",
        )
    return run_tencent_quote_shadow(
        securities=securities,
        run_context=run_context,
        settings=settings,
        store=store,
        provider_factory=provider_factory,
        clock=clock,
        monotonic=monotonic,
        metric_emitter=metric_emitter,
    )
