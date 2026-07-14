from __future__ import annotations

from datetime import datetime, timezone

import pytest

from daily_report_agent.config import TencentQuoteShadowSettings
from daily_report_agent.models.issues import DataIssue, IssueCategory, IssueSeverity
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.security import Security
from daily_report_agent.pipeline.context import RunContext
from daily_report_agent.pipeline.tencent_quote_shadow import (
    SequentialTencentQuoteTransport,
    TencentQuoteShadowResult,
    maybe_run_tencent_quote_shadow,
    run_tencent_quote_shadow,
)
from daily_report_agent.pipeline import tencent_quote_shadow as shadow_module
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.errors import ProviderNetworkError
from daily_report_agent.providers.tencent.constants import TENCENT_QUOTE_DESCRIPTOR


WHEN = datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)
SETTINGS = TencentQuoteShadowSettings(True, 5.0, 20, 20)
SECURITIES = (
    Security("cn", "600519", "贵州茅台"),
    Security("cn", "000001", "平安银行"),
)


def _context(active: bool = True) -> RunContext:
    return RunContext(
        run_id="run-shadow",
        started_at=WHEN,
        storage_enabled=active,
        database=object() if active else None,  # type: ignore[arg-type]
        pipeline_run_id="run-shadow" if active else None,
    )


def _snapshot(symbol: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        observed_at=WHEN,
        source="tencent-finance",
        price=10.0,
        previous_close=10.0,
        pct_change=0.0,
    )


class FakeProvider:
    descriptor = TENCENT_QUOTE_DESCRIPTOR

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = []

    def fetch_quotes(self, securities):
        self.calls.append(securities)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeStore:
    def __init__(self, *, fail_start=False, fail_finish=False, fail_symbol=None) -> None:
        self.fail_start = fail_start
        self.fail_finish = fail_finish
        self.fail_symbol = fail_symbol
        self.starts = []
        self.saved = []
        self.finishes = []

    def start_call(self, **kwargs):
        self.starts.append(kwargs)
        if self.fail_start:
            raise RuntimeError("database path and secret must not leak")
        return 42

    def save_snapshot(self, **kwargs):
        if kwargs["snapshot"].symbol == self.fail_symbol:
            raise RuntimeError("price=10 database=/private/secret")
        self.saved.append(kwargs)
        return True

    def finish_call(self, call_id, status, finished_at, **kwargs):
        self.finishes.append((call_id, status, finished_at, kwargs))
        if self.fail_finish:
            raise RuntimeError("unsafe finish failure")


def _ticks():
    values = iter((10.0, 10.125))
    return lambda: next(values)


def _run(outcome, *, store=None):
    provider = FakeProvider(outcome)
    store = store or FakeStore()
    result = run_tencent_quote_shadow(
        securities=SECURITIES,
        run_context=_context(),
        settings=SETTINGS,
        store=store,
        provider_factory=lambda settings: provider,
        clock=lambda: WHEN,
        monotonic=_ticks(),
    )
    return result, provider, store


def test_success_calls_provider_once_and_finishes_success() -> None:
    outcome = ProviderResult(
        provider=TENCENT_QUOTE_DESCRIPTOR,
        items=(_snapshot("600519"), _snapshot("000001")),
    )
    result, provider, store = _run(outcome)
    assert provider.calls == [SECURITIES]
    assert result == TencentQuoteShadowResult(True, "success", 2, 2, 2, 0, 42, 125)
    assert store.finishes[0][1] == "success"
    assert store.finishes[0][3]["item_count"] == 2
    assert store.finishes[0][3]["duration_ms"] == 125


def test_empty_result_finishes_empty_without_snapshots() -> None:
    result, _, store = _run(ProviderResult(provider=TENCENT_QUOTE_DESCRIPTOR))
    assert result.status == "empty"
    assert result.received_count == 0
    assert store.saved == []
    assert store.finishes[0][1] == "empty"


def test_provider_error_is_safely_recorded_and_isolated(capsys) -> None:
    error = ProviderNetworkError(
        provider_id="tencent-finance",
        operation="fetch_quotes",
        safe_message="network failed",
        code="network_error",
    )
    result, provider, store = _run(error)
    finish = store.finishes[0]
    assert len(provider.calls) == 1
    assert result.status == "failed"
    assert finish[1] == "failed"
    assert finish[3]["error_category"] == "network"
    assert finish[3]["error_code"] == "network_error"
    assert "traceback" not in capsys.readouterr().out.lower()


def test_error_issue_and_snapshot_produce_partial_but_provider_call_success() -> None:
    issue = DataIssue(
        IssueSeverity.ERROR,
        IssueCategory.PARSE,
        "tencent-finance",
        "fetch_quotes",
        "safe parse issue",
        False,
        WHEN,
        code="malformed_record",
    )
    outcome = ProviderResult(
        provider=TENCENT_QUOTE_DESCRIPTOR,
        items=(_snapshot("600519"),),
        issues=(issue,),
    )
    result, _, store = _run(outcome)
    assert result.status == "partial"
    assert result.issue_count == 1
    assert store.finishes[0][1] == "success"


def test_warning_issue_does_not_turn_usable_result_partial() -> None:
    issue = DataIssue(
        IssueSeverity.WARNING,
        IssueCategory.MISSING_DATA,
        "tencent-finance",
        "fetch_quotes",
        "safe warning",
        False,
        WHEN,
    )
    result, _, _ = _run(
        ProviderResult(
            provider=TENCENT_QUOTE_DESCRIPTOR,
            items=(_snapshot("600519"),),
            issues=(issue,),
        )
    )
    assert result.status == "success"
    assert result.issue_count == 1


def test_single_snapshot_failure_continues_other_items_and_is_safe(capsys) -> None:
    store = FakeStore(fail_symbol="600519")
    outcome = ProviderResult(
        provider=TENCENT_QUOTE_DESCRIPTOR,
        items=(_snapshot("600519"), _snapshot("000001")),
    )
    result, _, store = _run(outcome, store=store)
    assert result.status == "partial"
    assert result.created_snapshot_count == 1
    assert result.issue_count == 1
    assert [item["snapshot"].symbol for item in store.saved] == ["000001"]
    output = capsys.readouterr().out
    assert "10" not in output
    assert "/private" not in output


def test_start_failure_does_not_construct_or_call_provider() -> None:
    store = FakeStore(fail_start=True)
    constructed = []
    result = run_tencent_quote_shadow(
        securities=SECURITIES,
        run_context=_context(),
        settings=SETTINGS,
        store=store,
        provider_factory=lambda settings: constructed.append(settings),  # type: ignore[return-value]
        clock=lambda: WHEN,
        monotonic=lambda: 10.0,
    )
    assert result.status == "skipped"
    assert result.executed is False
    assert constructed == []


def test_finish_failure_does_not_retry_provider_or_snapshots() -> None:
    store = FakeStore(fail_finish=True)
    result, provider, store = _run(
        ProviderResult(provider=TENCENT_QUOTE_DESCRIPTOR, items=(_snapshot("600519"),)),
        store=store,
    )
    assert result.status == "partial"
    assert result.safe_message == "provider_call_finish_failed"
    assert len(provider.calls) == 1
    assert len(store.saved) == 1
    assert len(store.finishes) == 1


@pytest.mark.parametrize("dry_run,enabled", [(True, True), (False, False)])
def test_dry_run_and_disabled_shadow_never_build_store_or_provider(dry_run, enabled) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("closed shadow must have no side effects")

    result = maybe_run_tencent_quote_shadow(
        config={"providers": {"tencent_quote": {"shadow_enabled": enabled}}},
        watchlist=[{"market": "cn", "symbol": "600519", "name": "茅台"}],
        run_context=_context(),
        dry_run=dry_run,
        provider_factory=fail,
        store_factory=fail,
    )
    assert result.status == "disabled"


def test_enabled_shadow_with_inactive_storage_warns_and_does_not_construct(capsys) -> None:
    result = maybe_run_tencent_quote_shadow(
        config={"providers": {"tencent_quote": {"shadow_enabled": True}}},
        watchlist=[{"market": "cn", "symbol": "600519", "name": "茅台"}],
        run_context=_context(False),
        dry_run=False,
        provider_factory=lambda settings: (_ for _ in ()).throw(AssertionError()),
        store_factory=lambda context: (_ for _ in ()).throw(AssertionError()),
    )
    assert result.status == "skipped"
    assert "存储未启用" in capsys.readouterr().out


def test_us_only_watchlist_skips_without_store_or_provider() -> None:
    result = maybe_run_tencent_quote_shadow(
        config={"providers": {"tencent_quote": {"shadow_enabled": True}}},
        watchlist=[{"market": "us", "symbol": "AAPL", "name": "苹果"}],
        run_context=_context(),
        dry_run=False,
        provider_factory=lambda settings: (_ for _ in ()).throw(AssertionError()),
        store_factory=lambda context: (_ for _ in ()).throw(AssertionError()),
    )
    assert result.status == "skipped"
    assert result.requested_count == 0


def test_store_initialization_failure_happens_before_provider_construction() -> None:
    constructed = []
    result = maybe_run_tencent_quote_shadow(
        config={"providers": {"tencent_quote": {"shadow_enabled": True}}},
        watchlist=[{"market": "cn", "symbol": "600519", "name": "茅台"}],
        run_context=_context(),
        dry_run=False,
        provider_factory=lambda settings: constructed.append(settings),  # type: ignore[return-value]
        store_factory=lambda context: (_ for _ in ()).throw(RuntimeError("unsafe")),
    )
    assert result.status == "skipped"
    assert result.safe_message == "store_initialization_failed"
    assert constructed == []


def test_max_symbols_is_applied_before_provider_call() -> None:
    provider = FakeProvider(
        ProviderResult(provider=TENCENT_QUOTE_DESCRIPTOR, items=(_snapshot("600519"),))
    )
    result = maybe_run_tencent_quote_shadow(
        config={
            "providers": {
                "tencent_quote": {"shadow_enabled": True, "max_symbols": 1}
            }
        },
        watchlist=[
            {"market": "cn", "symbol": "600519", "name": "茅台"},
            {"market": "cn", "symbol": "000001", "name": "平安"},
        ],
        run_context=_context(),
        dry_run=False,
        provider_factory=lambda settings: provider,
        store_factory=lambda context: FakeStore(),
        clock=lambda: WHEN,
        monotonic=_ticks(),
    )
    assert result.requested_count == 1
    assert [item.symbol for item in provider.calls[0]] == ["600519"]


def test_sequential_transport_honors_online_limit_without_retry_or_concurrency() -> None:
    class Transport:
        def __init__(self):
            self.calls = []

        def fetch_quote_text(self, symbols, *, timeout_seconds):
            self.calls.append((symbols, timeout_seconds))
            return f"response-{len(self.calls)}"

    base = Transport()
    transport = SequentialTencentQuoteTransport(base, max_symbols_per_request=5)
    symbols = tuple(f"sh{index:06d}" for index in range(12))
    text = transport.fetch_quote_text(symbols, timeout_seconds=7)
    assert [len(call[0]) for call in base.calls] == [5, 5, 2]
    assert all(call[1] == 7 for call in base.calls)
    assert text == "response-1\nresponse-2\nresponse-3"


def test_default_factory_passes_configured_timeout_and_batch_without_network(
    monkeypatch,
) -> None:
    from daily_report_agent.providers.tencent import online_transport, quote

    captured = {}

    class Transport:
        pass

    class Provider:
        def __init__(self, transport, *, timeout_seconds, batch_size):
            captured.update(
                transport=transport,
                timeout_seconds=timeout_seconds,
                batch_size=batch_size,
            )

    monkeypatch.setattr(online_transport, "TencentOnlineQuoteTransport", Transport)
    monkeypatch.setattr(quote, "TencentQuoteProvider", Provider)
    provider = shadow_module._default_provider_factory(SETTINGS)
    assert isinstance(provider, Provider)
    assert isinstance(captured["transport"], SequentialTencentQuoteTransport)
    assert captured["timeout_seconds"] == 5.0
    assert captured["batch_size"] == 20
