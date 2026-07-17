"""Explicit, isolated Tencent quote Shadow observation entry point."""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daily_report_agent.config import TencentQuoteShadowSettings
from daily_report_agent.models.security import Security
from daily_report_agent.pipeline.context import RunContext
from daily_report_agent.pipeline.tencent_quote_shadow import (
    SQLiteTencentQuoteShadowStore,
    SequentialTencentQuoteTransport,
    run_tencent_quote_shadow,
)
from daily_report_agent.providers.errors import ProviderValidationError
from daily_report_agent.providers.tencent.symbols import to_tencent_symbol
from daily_report_agent.storage.database import Database
from daily_report_agent.storage.repositories import (
    PipelineRunRepository,
    ProviderCallRepository,
)


MAX_OBSERVATION_SYMBOLS = 5
DEFAULT_TIMEOUT_SECONDS = 10.0
FORMAL_DEFAULT_DATABASE = (ROOT / "daily_report_agent" / "data" / "agent.db").resolve()


class _QuoteTransport(Protocol):
    def fetch_quote_text(
        self,
        symbols: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> str:
        ...


class _QuoteProvider(Protocol):
    def fetch_quotes(self, securities: tuple[Security, ...]):
        ...


@dataclass(slots=True)
class _RuntimeProbe:
    logical_calls: int = 0
    http_requests: int = 0
    returned_symbols: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()


class _CountingTransport:
    def __init__(self, transport: _QuoteTransport, probe: _RuntimeProbe) -> None:
        self._transport = transport
        self._probe = probe

    def fetch_quote_text(
        self,
        symbols: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> str:
        self._probe.http_requests += 1
        return self._transport.fetch_quote_text(
            symbols,
            timeout_seconds=timeout_seconds,
        )


class _ObservedProvider:
    def __init__(self, provider: _QuoteProvider, probe: _RuntimeProbe) -> None:
        self._provider = provider
        self._probe = probe

    def fetch_quotes(self, securities: tuple[Security, ...]):
        self._probe.logical_calls += 1
        result = self._provider.fetch_quotes(securities)
        self._probe.returned_symbols = tuple(item.symbol for item in result.items)
        self._probe.issue_codes = tuple(
            issue.code or issue.category.value for issue in result.issues
        )
        return result


@dataclass(frozen=True, slots=True)
class ObservationSummary:
    success: bool
    status: str
    requested_count: int
    received_count: int
    missing_symbols: tuple[str, ...]
    logical_calls: int
    http_requests: int
    duration_ms: int | None
    created_snapshots: int
    created_provider_calls: int
    created_raw_responses: int
    issue_count: int
    issue_codes: tuple[str, ...]
    database_path: Path
    run_id: str
    provider_call_id: int | None

    def lines(self) -> tuple[str, ...]:
        return (
            f"observation_success: {str(self.success).lower()}",
            f"status: {self.status}",
            f"requested: {self.requested_count}",
            f"received: {self.received_count}",
            "missing_symbols: " + (",".join(self.missing_symbols) or "none"),
            f"logical_fetch_calls: {self.logical_calls}",
            f"http_requests: {self.http_requests}",
            "provider_duration_ms: "
            + ("unavailable" if self.duration_ms is None else str(self.duration_ms)),
            f"market_snapshots_added: {self.created_snapshots}",
            f"provider_calls_added: {self.created_provider_calls}",
            f"raw_responses_added: {self.created_raw_responses}",
            f"data_issues: {self.issue_count}",
            "issue_codes: " + (",".join(self.issue_codes) or "none"),
            f"database_path: {self.database_path}",
            f"run_id: {self.run_id}",
            "provider_call_id: "
            + ("none" if self.provider_call_id is None else str(self.provider_call_id)),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one isolated Tencent quote Shadow observation"
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="explicitly allow the controlled Tencent network request",
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    return parser


def _positive_timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--timeout must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0 or parsed > 60:
        raise argparse.ArgumentTypeError("--timeout must be within (0, 60]")
    return parsed


def _parse_symbols(values: list[str]) -> tuple[Security, ...]:
    ordered: list[Security] = []
    seen: set[str] = set()
    for raw in values:
        symbol = str(raw).strip()
        if symbol in seen:
            continue
        security = Security(market="cn", symbol=symbol, name=symbol)
        try:
            to_tencent_symbol(security)
        except ProviderValidationError as exc:
            raise ValueError(exc.safe_message) from None
        seen.add(symbol)
        ordered.append(security)
    if not ordered:
        raise ValueError("--symbols requires at least one supported A-share symbol")
    if len(ordered) > MAX_OBSERVATION_SYMBOLS:
        raise ValueError(
            f"--symbols accepts at most {MAX_OBSERVATION_SYMBOLS} unique symbols"
        )
    return tuple(ordered)


def _database_path(value: str) -> Path:
    normalized = value.strip()
    if not normalized or normalized == ":memory:":
        raise ValueError("--db-path must name an independent SQLite file")
    path = Path(normalized).expanduser().resolve()
    if path == FORMAL_DEFAULT_DATABASE:
        raise ValueError("--db-path must not target the formal default database")
    if path.exists() and not path.is_file():
        raise ValueError("--db-path must name a file, not a directory")
    return path


def _default_transport_factory() -> _QuoteTransport:
    # Delayed import keeps the network-capable component outside the CLI deny path.
    from daily_report_agent.providers.tencent.online_transport import (
        TencentOnlineQuoteTransport,
    )

    return TencentOnlineQuoteTransport()


def _new_run_id() -> str:
    return f"tencent-shadow-observe-{uuid4()}"


def _table_counts(database: Database) -> dict[str, int]:
    with closing(database.connect()) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("market_snapshots", "provider_calls", "raw_responses")
        }


def _pipeline_status(shadow_status: str) -> str:
    if shadow_status == "success":
        return "success"
    if shadow_status in {"partial", "empty"}:
        return "partial"
    return "failed"


def observe_once(
    *,
    database_path: Path,
    securities: tuple[Security, ...],
    timeout_seconds: float,
    transport_factory: Callable[[], _QuoteTransport] = _default_transport_factory,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    run_id_factory: Callable[[], str] = _new_run_id,
) -> ObservationSummary:
    """Run exactly one existing Shadow service call and summarize persisted deltas."""
    if not isinstance(securities, tuple) or not 1 <= len(securities) <= MAX_OBSERVATION_SYMBOLS:
        raise ValueError("securities must contain between one and five items")
    mapped_symbols = tuple(to_tencent_symbol(security) for security in securities)
    if len(mapped_symbols) != len(set(mapped_symbols)):
        raise ValueError("securities must not contain duplicates")
    settings = TencentQuoteShadowSettings(
        enabled=True,
        timeout_seconds=timeout_seconds,
        batch_size=MAX_OBSERVATION_SYMBOLS,
        max_symbols=MAX_OBSERVATION_SYMBOLS,
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(database_path)
    database.initialize()
    before = _table_counts(database)

    run_id = run_id_factory()
    started_at = clock()
    with database.transaction() as connection:
        PipelineRunRepository(connection).start_run(
            run_id,
            started_at,
            dry_run=False,
            app_version="tencent-shadow-observe-v1",
        )
    context = RunContext(
        run_id=run_id,
        started_at=started_at,
        storage_enabled=True,
        database=database,
        pipeline_run_id=run_id,
    )
    probe = _RuntimeProbe()

    def provider_factory(current: TencentQuoteShadowSettings):
        from daily_report_agent.providers.tencent.online_transport import (
            MAX_ONLINE_SYMBOLS,
        )
        from daily_report_agent.providers.tencent.quote import TencentQuoteProvider

        transport = _CountingTransport(transport_factory(), probe)
        provider = TencentQuoteProvider(
            SequentialTencentQuoteTransport(
                transport,
                max_symbols_per_request=MAX_ONLINE_SYMBOLS,
            ),
            timeout_seconds=current.timeout_seconds,
            batch_size=current.batch_size,
            clock=clock,
        )
        return _ObservedProvider(provider, probe)

    try:
        result = run_tencent_quote_shadow(
            securities=securities,
            run_context=context,
            settings=settings,
            store=SQLiteTencentQuoteShadowStore(context),
            provider_factory=provider_factory,
            clock=clock,
            monotonic=monotonic,
        )
    except Exception:
        with database.transaction() as connection:
            PipelineRunRepository(connection).finish_run(
                run_id,
                "failed",
                clock(),
                error_summary="Tencent shadow observation failed unexpectedly",
            )
        raise

    with database.transaction() as connection:
        PipelineRunRepository(connection).finish_run(
            run_id,
            _pipeline_status(result.status),
            clock(),
            error_summary=(
                None
                if result.status == "success"
                else f"Tencent shadow observation ended with status {result.status}"
            ),
            created_snapshots=result.created_snapshot_count,
        )

    after = _table_counts(database)
    provider_error_code = None
    if result.provider_call_id is not None:
        with closing(database.connect()) as connection:
            call = ProviderCallRepository(connection).get_call(result.provider_call_id)
            provider_error_code = call.error_code if call is not None else None

    returned = set(probe.returned_symbols)
    missing = tuple(
        security.symbol for security in securities if security.symbol not in returned
    )
    issue_codes = probe.issue_codes
    if provider_error_code is not None and provider_error_code not in issue_codes:
        issue_codes += (provider_error_code,)

    return ObservationSummary(
        success=result.status == "success",
        status=result.status,
        requested_count=result.requested_count,
        received_count=result.received_count,
        missing_symbols=missing,
        logical_calls=probe.logical_calls,
        http_requests=probe.http_requests,
        duration_ms=result.duration_ms,
        created_snapshots=after["market_snapshots"] - before["market_snapshots"],
        created_provider_calls=after["provider_calls"] - before["provider_calls"],
        created_raw_responses=after["raw_responses"] - before["raw_responses"],
        issue_count=result.issue_count,
        issue_codes=issue_codes,
        database_path=database_path,
        run_id=run_id,
        provider_call_id=result.provider_call_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_network:
        print(
            "network disabled: pass --allow-network explicitly; no database was opened",
            file=sys.stderr,
        )
        return 2
    try:
        securities = _parse_symbols(args.symbols)
        database_path = _database_path(args.db_path)
    except (TypeError, ValueError) as exc:
        print(f"observation rejected: {exc}", file=sys.stderr)
        return 2

    try:
        summary = observe_once(
            database_path=database_path,
            securities=securities,
            timeout_seconds=args.timeout,
        )
    except Exception as exc:
        print(f"observation failed safely: {type(exc).__name__}", file=sys.stderr)
        return 1

    print("\n".join(summary.lines()))
    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
