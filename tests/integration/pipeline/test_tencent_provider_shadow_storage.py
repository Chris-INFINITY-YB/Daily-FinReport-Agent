from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_report_agent.config import parse_provider_routing_settings
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.pipeline.tencent_provider_shadow import (
    TENCENT_SHADOW_KEY,
    run_tencent_provider_shadow_from_config,
    validate_tencent_provider_shadow_gate,
)
from daily_report_agent.providers.circuit_breaker import (
    CircuitBreakerPolicy,
    CircuitBreakerState,
)
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.errors import (
    ProviderNetworkError,
    ProviderTimeoutError,
)
from daily_report_agent.providers.routing import (
    ErrorClassification,
    ProviderErrorClass,
    RetryErrorCode,
    RouteTerminalStatus,
)
from daily_report_agent.providers.tencent.constants import (
    TENCENT_QUOTE_DESCRIPTOR,
)
from daily_report_agent.storage.circuit_breaker import (
    SQLiteCircuitBreakerStore,
)
from daily_report_agent.storage.database import Database


NOW = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
WATCHLIST = [
    {"market": "cn", "symbol": "600519", "name": "贵州茅台"},
    {"market": "cn", "symbol": "000001", "name": "平安银行"},
    {"market": "us", "symbol": "AAPL", "name": "Apple"},
]


def _snapshot(symbol: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        observed_at=NOW,
        source="tencent-finance",
        price=10.0,
        previous_close=9.0,
        pct_change=1.0,
        currency="CNY",
    )


class FakeProvider:
    descriptor = TENCENT_QUOTE_DESCRIPTOR

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def fetch_quotes(self, securities):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _setup(tmp_path: Path):
    shadow_path = tmp_path / "shadow.sqlite"
    formal_path = tmp_path / "formal.sqlite"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("offline fixture config", encoding="utf-8")
    config = {
        "pipeline": {
            "data_route": "provider_shadow",
            "max_provider_calls": 1,
            "provider_shadow_database_path": str(shadow_path),
        },
        "storage": {"enabled": True, "path": str(formal_path)},
        "providers": {
            "tencent_quote": {
                "shadow_enabled": True,
                "max_symbols": 5,
                "batch_size": 5,
            }
        },
    }
    gate = validate_tencent_provider_shadow_gate(
        config=config,
        config_path=str(config_path),
        routing=parse_provider_routing_settings(config),
        allow_provider_shadow=True,
        dry_run=False,
    )
    assert gate is not None
    shadow = Database(shadow_path)
    shadow.initialize()
    formal = Database(formal_path)
    formal.initialize()
    return config_path, gate, shadow, formal


def _run(
    config_path: Path,
    gate,
    shadow: Database,
    provider: FakeProvider,
    *,
    clock=lambda: NOW,
    store=None,
    policy=CircuitBreakerPolicy(),
):
    return run_tencent_provider_shadow_from_config(
        gate=gate,
        config_path=str(config_path),
        watchlist=WATCHLIST,
        provider_factory=lambda settings: provider,
        database=shadow,
        circuit_store=store,
        clock=clock,
        monotonic=lambda: 10.0,
        metric_sink=lambda event: None,
        circuit_policy=policy,
    )


def _count(database: Database, table: str) -> int:
    with closing(database.connect()) as connection:
        return connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]


def test_success_persists_independent_run_call_securities_snapshots_and_circuit(
    tmp_path: Path,
) -> None:
    config_path, gate, shadow, formal = _setup(tmp_path)
    provider = FakeProvider(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"), _snapshot("000001")),
        )
    )
    result = _run(config_path, gate, shadow, provider)

    assert result.status is RouteTerminalStatus.SUCCESS
    assert result.call_budget_used == 1
    assert provider.calls == 1
    assert {
        table: _count(shadow, table)
        for table in (
            "pipeline_runs",
            "provider_calls",
            "securities",
            "market_snapshots",
            "raw_responses",
            "provider_circuit_breakers",
        )
    } == {
        "pipeline_runs": 1,
        "provider_calls": 1,
        "securities": 2,
        "market_snapshots": 2,
        "raw_responses": 0,
        "provider_circuit_breakers": 1,
    }
    assert _count(formal, "pipeline_runs") == 0
    assert _count(formal, "provider_calls") == 0
    assert _count(formal, "market_snapshots") == 0
    with closing(shadow.connect()) as connection:
        call = connection.execute(
            "SELECT status, item_count, retry_count FROM provider_calls"
        ).fetchone()
        run = connection.execute(
            "SELECT status, created_snapshots FROM pipeline_runs"
        ).fetchone()
    assert tuple(call) == ("success", 2, 0)
    assert tuple(run) == ("success", 2)


def test_partial_preserves_valid_snapshot_and_records_partial_pipeline(
    tmp_path: Path,
) -> None:
    config_path, gate, shadow, _ = _setup(tmp_path)
    provider = FakeProvider(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"),),
        )
    )
    result = _run(config_path, gate, shadow, provider)
    with closing(shadow.connect()) as connection:
        call = connection.execute(
            "SELECT status, item_count, retry_count FROM provider_calls"
        ).fetchone()
        run_status = connection.execute(
            "SELECT status FROM pipeline_runs"
        ).fetchone()[0]
    assert result.status is RouteTerminalStatus.PARTIAL
    assert result.item_count == 1
    assert result.issue_count == 1
    assert _count(shadow, "market_snapshots") == 1
    assert tuple(call) == ("success", 1, 0)
    assert run_status == "partial"


def test_provider_failure_records_call_and_circuit_without_retry(
    tmp_path: Path,
) -> None:
    config_path, gate, shadow, _ = _setup(tmp_path)
    provider = FakeProvider(
        ProviderNetworkError(
            provider_id="tencent-finance",
            operation="fetch_quotes",
            safe_message="safe fixture network failure",
            code="fixture_network",
        )
    )
    result = _run(config_path, gate, shadow, provider)
    persisted = SQLiteCircuitBreakerStore(shadow).load(TENCENT_SHADOW_KEY)
    with closing(shadow.connect()) as connection:
        call = connection.execute(
            "SELECT status, item_count, retry_count FROM provider_calls"
        ).fetchone()
    assert result.status is RouteTerminalStatus.FAILED
    assert result.call_budget_used == 1
    assert provider.calls == 1
    assert tuple(call) == ("failed", 0, 0)
    assert persisted is not None
    assert persisted.snapshot.consecutive_failures == 1
    assert _count(shadow, "raw_responses") == 0


def _open_circuit(
    store: SQLiteCircuitBreakerStore,
    policy: CircuitBreakerPolicy,
) -> None:
    preflight = store.preflight(TENCENT_SHADOW_KEY, policy, now=NOW)
    store.record_failure(
        TENCENT_SHADOW_KEY,
        policy,
        ErrorClassification(
            RetryErrorCode.TIMEOUT,
            ProviderErrorClass.TRANSIENT,
        ),
        expected_version=preflight.version,
        now=NOW,
    )


def test_open_skip_does_not_construct_provider_or_consume_budget(
    tmp_path: Path,
) -> None:
    config_path, gate, shadow, _ = _setup(tmp_path)
    store = SQLiteCircuitBreakerStore(shadow)
    policy = CircuitBreakerPolicy(
        failure_threshold=1,
        open_duration=timedelta(minutes=1),
    )
    _open_circuit(store, policy)
    provider = FakeProvider(AssertionError("must not be called"))
    result = _run(
        config_path,
        gate,
        shadow,
        provider,
        clock=lambda: NOW + timedelta(seconds=1),
        store=store,
        policy=policy,
    )
    with closing(shadow.connect()) as connection:
        call_status = connection.execute(
            "SELECT status FROM provider_calls"
        ).fetchone()[0]
    assert result.status is RouteTerminalStatus.SKIPPED
    assert result.safe_error_code == "circuit_open"
    assert result.call_budget_used == 0
    assert provider.calls == 0
    assert call_status == "skipped"


def test_expired_probe_success_closes_and_failure_reopens(
    tmp_path: Path,
) -> None:
    for index, succeeds in enumerate((True, False)):
        case = tmp_path / str(index)
        case.mkdir()
        config_path, gate, shadow, _ = _setup(case)
        store = SQLiteCircuitBreakerStore(shadow)
        policy = CircuitBreakerPolicy(
            failure_threshold=1,
            open_duration=timedelta(seconds=1),
        )
        _open_circuit(store, policy)
        outcome = (
            ProviderResult(
                TENCENT_QUOTE_DESCRIPTOR,
                (_snapshot("600519"), _snapshot("000001")),
            )
            if succeeds
            else ProviderTimeoutError(
                provider_id="tencent-finance",
                operation="fetch_quotes",
                safe_message="safe fixture timeout",
                code="fixture_timeout",
            )
        )
        provider = FakeProvider(outcome)
        _run(
            config_path,
            gate,
            shadow,
            provider,
            clock=lambda: NOW + timedelta(seconds=2),
            store=store,
            policy=policy,
        )
        persisted = store.load(TENCENT_SHADOW_KEY)
        assert persisted is not None
        assert persisted.snapshot.state is (
            CircuitBreakerState.CLOSED
            if succeeds
            else CircuitBreakerState.OPEN
        )
        assert provider.calls == 1


def test_repeated_snapshot_is_idempotent_but_each_run_is_audited(
    tmp_path: Path,
) -> None:
    config_path, gate, shadow, _ = _setup(tmp_path)
    provider = FakeProvider(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"), _snapshot("000001")),
        )
    )
    first = _run(config_path, gate, shadow, provider)
    second = _run(config_path, gate, shadow, provider)
    assert first.created_snapshot_count == 2
    assert second.created_snapshot_count == 0
    assert _count(shadow, "market_snapshots") == 2
    assert _count(shadow, "pipeline_runs") == 2
    assert _count(shadow, "provider_calls") == 2
    assert _count(shadow, "raw_responses") == 0
