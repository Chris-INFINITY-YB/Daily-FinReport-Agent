from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from daily_report_agent.config import parse_provider_routing_settings
from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.security import Security
from daily_report_agent.pipeline.tencent_provider_shadow import (
    ProviderShadowGateError,
    ProviderShadowGateErrorCode,
    TencentProviderShadowOrchestrator,
    TencentQuoteResultEvaluator,
    TencentShadowInput,
    build_tencent_shadow_policy,
    build_tencent_shadow_registry,
    select_tencent_shadow_securities,
    validate_tencent_provider_shadow_gate,
)
from daily_report_agent.providers.circuit_breaker import (
    CircuitDecision,
    CircuitOutcomeOrigin,
)
from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderResult,
)
from daily_report_agent.providers.errors import (
    ProviderBlockedError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from daily_report_agent.providers.routing import (
    DataRouteMode,
    ProviderRuntimeStage,
    RouteTerminalStatus,
)
from daily_report_agent.providers.tencent.constants import (
    TENCENT_QUOTE_DESCRIPTOR,
)


NOW = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
SECURITIES = (
    Security("cn", "600519", "贵州茅台"),
    Security("cn", "000001", "平安银行"),
)


def _config(shadow_path: object = "shadow.sqlite") -> dict:
    return {
        "pipeline": {
            "data_route": "provider_shadow",
            "max_provider_calls": 1,
            "provider_shadow_database_path": shadow_path,
        },
        "storage": {"enabled": False, "path": "formal.sqlite"},
        "providers": {
            "tencent_quote": {
                "shadow_enabled": True,
                "max_symbols": 5,
            }
        },
    }


def _gate(
    tmp_path: Path,
    config: dict,
    *,
    allow: bool = True,
    dry_run: bool = False,
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("fixture", encoding="utf-8")
    return validate_tencent_provider_shadow_gate(
        config=config,
        config_path=str(config_path),
        routing=parse_provider_routing_settings(config),
        allow_provider_shadow=allow,
        dry_run=dry_run,
    )


def test_legacy_gate_returns_without_parsing_shadow_config(tmp_path: Path) -> None:
    config = {
        "pipeline": {"data_route": "legacy"},
        "providers": {"tencent_quote": "not-a-mapping"},
    }
    assert _gate(tmp_path, config, allow=False, dry_run=True) is None


@pytest.mark.parametrize(
    ("change", "allow", "dry_run", "code"),
    [
        (
            lambda cfg: cfg["providers"]["tencent_quote"].update(
                shadow_enabled=False
            ),
            True,
            False,
            ProviderShadowGateErrorCode.SHADOW_NOT_ENABLED,
        ),
        (
            lambda cfg: None,
            False,
            False,
            ProviderShadowGateErrorCode.CLI_GATE_REQUIRED,
        ),
        (
            lambda cfg: None,
            True,
            True,
            ProviderShadowGateErrorCode.DRY_RUN_REJECTED,
        ),
        (
            lambda cfg: cfg["pipeline"].update(
                provider_shadow_database_path=None
            ),
            True,
            False,
            ProviderShadowGateErrorCode.DATABASE_PATH_REQUIRED,
        ),
        (
            lambda cfg: cfg["pipeline"].update(max_provider_calls=2),
            True,
            False,
            ProviderShadowGateErrorCode.CALL_BUDGET_INVALID,
        ),
        (
            lambda cfg: cfg["pipeline"].update(
                provider_priorities={"eastmoney": 0}
            ),
            True,
            False,
            ProviderShadowGateErrorCode.CANDIDATES_INVALID,
        ),
    ],
)
def test_shadow_gate_rejects_missing_or_unsafe_conditions(
    tmp_path: Path,
    change,
    allow: bool,
    dry_run: bool,
    code: ProviderShadowGateErrorCode,
) -> None:
    config = _config()
    change(config)
    with pytest.raises(ProviderShadowGateError) as raised:
        _gate(tmp_path, config, allow=allow, dry_run=dry_run)
    assert raised.value.code is code


def test_primary_gate_is_always_rejected(tmp_path: Path) -> None:
    config = _config()
    config["pipeline"]["data_route"] = "provider_primary"
    with pytest.raises(ProviderShadowGateError) as raised:
        _gate(tmp_path, config)
    assert raised.value.code is ProviderShadowGateErrorCode.STAGE_NOT_ENABLED


def test_shadow_database_must_be_distinct_and_not_directory(
    tmp_path: Path,
) -> None:
    config = _config("formal.sqlite")
    with pytest.raises(ProviderShadowGateError) as same:
        _gate(tmp_path, config)
    assert same.value.code is ProviderShadowGateErrorCode.DATABASE_PATH_CONFLICT

    directory = tmp_path / "shadow-dir"
    directory.mkdir()
    config = _config(str(directory))
    with pytest.raises(ProviderShadowGateError) as is_directory:
        _gate(tmp_path, config)
    assert is_directory.value.code is (
        ProviderShadowGateErrorCode.DATABASE_PATH_INVALID
    )


@pytest.mark.parametrize("value", ["", "  ", 3, True, [], {}])
def test_shadow_database_path_type_is_strict(value: object) -> None:
    with pytest.raises(ValueError, match="provider_shadow_database_path"):
        parse_provider_routing_settings(
            {
                "pipeline": {
                    "provider_shadow_database_path": value,
                }
            }
        )


def test_input_selection_is_cn_only_stable_deduplicated_and_capped() -> None:
    selected = select_tencent_shadow_securities(
        [
            {"market": "us", "symbol": "AAPL", "name": "Apple"},
            {"market": "cn", "symbol": "600519", "name": "茅台"},
            {"market": "cn", "symbol": "600519", "name": "duplicate"},
            {"market": "cn", "symbol": "920001", "name": "unsupported"},
            {"market": "cn", "symbol": "000001", "name": "平安"},
            {"market": "cn", "symbol": "300750", "name": "宁德"},
        ],
        max_symbols=2,
        now=NOW,
    )
    assert [item.symbol for item in selected.securities] == [
        "600519",
        "000001",
    ]
    assert [issue.code for issue in selected.issues] == [
        "duplicate_shadow_security",
        "unsupported_shadow_security",
    ]


def test_empty_cn_watchlist_has_no_security() -> None:
    selected = select_tencent_shadow_securities(
        [{"market": "us", "symbol": "AAPL", "name": "Apple"}],
        max_symbols=5,
        now=NOW,
    )
    assert selected == TencentShadowInput((), ())


def test_registry_and_policy_are_single_shadow_tencent_with_one_call() -> None:
    registry = build_tencent_shadow_registry(priority=7)
    registrations = registry.query(
        capability=ProviderCapability.QUOTE,
        market="cn",
        runtime_stage=ProviderRuntimeStage.SHADOW_ELIGIBLE,
    )
    policy = build_tencent_shadow_policy()
    assert len(registry) == 1
    assert registrations[0].provider_id == "tencent-finance"
    assert registrations[0].priority == 7
    assert registry.query(
        capability=registrations[0].capability,
        market="cn",
        runtime_stage=ProviderRuntimeStage.PRODUCTION_ELIGIBLE,
    ) == ()
    assert policy.mode is DataRouteMode.PROVIDER_SHADOW
    assert policy.max_call_budget == 1
    assert policy.allow_legacy_fallback is False
    assert policy.candidate_provider_ids == ("tencent-finance",)


def _snapshot(symbol: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        observed_at=NOW,
        source="tencent-finance",
        price=10.0,
    )


def _issue(severity: IssueSeverity) -> DataIssue:
    return DataIssue(
        severity,
        IssueCategory.PARSE,
        "tencent-finance",
        "fetch_quotes",
        "safe fixture issue",
        False,
        NOW,
        code="fixture_issue",
    )


def test_quote_evaluator_success_empty_partial_and_blocking_issue() -> None:
    evaluator = TencentQuoteResultEvaluator(SECURITIES)
    success = evaluator(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"), _snapshot("000001")),
            (_issue(IssueSeverity.WARNING),),
        )
    )
    empty = evaluator(ProviderResult(TENCENT_QUOTE_DESCRIPTOR))
    partial = evaluator(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"),),
        )
    )
    blocking = evaluator(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"), _snapshot("000001")),
            (_issue(IssueSeverity.ERROR),),
        )
    )
    assert success.terminal_status is RouteTerminalStatus.SUCCESS
    assert empty.terminal_status is RouteTerminalStatus.EMPTY
    assert partial.terminal_status is RouteTerminalStatus.PARTIAL
    assert blocking.terminal_status is RouteTerminalStatus.PARTIAL


class FakeCircuit:
    def __init__(self, decision=CircuitDecision.ALLOW, fail=None) -> None:
        self.decision = decision
        self.fail = fail
        self.preflights = 0
        self.successes = 0
        self.failures = []

    def preflight(self, key, policy, *, now):
        self.preflights += 1
        if self.fail == "preflight":
            raise RuntimeError("database=/secret")
        return SimpleNamespace(
            version=3,
            transition=SimpleNamespace(decision=self.decision),
        )

    def record_success(self, key, *, expected_version, now):
        if self.fail == "success":
            raise RuntimeError("stale version")
        self.successes += 1

    def record_failure(
        self,
        key,
        policy,
        classification,
        *,
        expected_version,
        now,
        origin,
    ):
        if self.fail == "failure":
            raise RuntimeError("stale version")
        self.failures.append((classification, origin))


class FakePersistence:
    def __init__(self, fail=None) -> None:
        self.fail = fail
        self.runs = []
        self.calls = []
        self.finishes = []
        self.snapshots = []

    def start_run(self, *args, **kwargs):
        if self.fail == "start_run":
            raise RuntimeError("unsafe path")
        self.runs.append((args, kwargs))

    def finish_run(self, *args, **kwargs):
        self.finishes.append(("run", args, kwargs))

    def start_call(self, *args, **kwargs):
        if self.fail == "start_call":
            raise RuntimeError("unsafe path")
        self.calls.append((args, kwargs))
        return 9

    def finish_call(self, *args, **kwargs):
        self.finishes.append(("call", args, kwargs))

    def save_snapshot(self, security, snapshot, *, fetched_at):
        self.snapshots.append((security, snapshot, fetched_at))
        return True


class OutcomeInvoker:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def __call__(self, registration):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _orchestrator(invoker, circuit=None, persistence=None, evaluator=None):
    return TencentProviderShadowOrchestrator(
        registry=build_tencent_shadow_registry(),
        invoker=invoker,
        evaluator=evaluator or TencentQuoteResultEvaluator(SECURITIES),
        circuit_store=circuit or FakeCircuit(),
        persistence=persistence or FakePersistence(),
        clock=lambda: NOW,
        monotonic=lambda: 10.0,
        metric_sink=lambda event: None,
    )


def test_closed_success_calls_once_uses_one_budget_and_persists_items() -> None:
    invoker = OutcomeInvoker(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"), _snapshot("000001")),
        )
    )
    circuit = FakeCircuit()
    persistence = FakePersistence()
    result = _orchestrator(invoker, circuit, persistence).execute(
        TencentShadowInput(SECURITIES, ()),
        run_id="shadow-run",
    )
    assert result.status is RouteTerminalStatus.SUCCESS
    assert result.call_budget_used == 1
    assert invoker.calls == 1
    assert circuit.successes == 1
    assert len(persistence.snapshots) == 2


def test_open_and_circuit_storage_failure_fail_closed_without_budget() -> None:
    for circuit, code in [
        (FakeCircuit(CircuitDecision.SKIP), "circuit_open"),
        (FakeCircuit(fail="preflight"), "circuit_storage_failed"),
    ]:
        invoker = OutcomeInvoker(AssertionError("must not call"))
        result = _orchestrator(invoker, circuit).execute(
            TencentShadowInput(SECURITIES, ())
        )
        assert result.status is RouteTerminalStatus.SKIPPED
        assert result.call_budget_used == 0
        assert result.safe_error_code == code
        assert invoker.calls == 0


@pytest.mark.parametrize(
    "error_type",
    [
        ProviderTimeoutError,
        ProviderNetworkError,
        ProviderUnavailableError,
        ProviderBlockedError,
        ProviderParseError,
    ],
)
def test_provider_failures_never_retry_or_fallback(error_type) -> None:
    invoker = OutcomeInvoker(
        error_type(
            provider_id="tencent-finance",
            operation="fetch_quotes",
            safe_message="safe fixture failure",
            code="fixture_failure",
        )
    )
    circuit = FakeCircuit()
    result = _orchestrator(invoker, circuit).execute(
        TencentShadowInput(SECURITIES, ())
    )
    assert result.status is RouteTerminalStatus.FAILED
    assert result.call_budget_used == 1
    assert invoker.calls == 1
    assert len(result.route_result.attempts) == 1
    assert len(result.route_result.attempts[0].retry_attempts) == 1
    assert len(circuit.failures) == 1


def test_circuit_outcome_conflict_does_not_repeat_provider_call() -> None:
    invoker = OutcomeInvoker(
        ProviderNetworkError(
            provider_id="tencent-finance",
            operation="fetch_quotes",
            safe_message="safe fixture failure",
            code="fixture_failure",
        )
    )
    result = _orchestrator(
        invoker,
        FakeCircuit(fail="failure"),
    ).execute(TencentShadowInput(SECURITIES, ()))
    assert result.safe_error_code == "circuit_storage_failed"
    assert result.call_budget_used == 1
    assert invoker.calls == 1


def test_evaluator_failure_is_circuit_neutral_and_control_signals_propagate() -> None:
    invoker = OutcomeInvoker(
        ProviderResult(
            TENCENT_QUOTE_DESCRIPTOR,
            (_snapshot("600519"),),
        )
    )
    circuit = FakeCircuit()

    def fail_evaluator(result):
        raise RuntimeError("evaluator detail")

    result = _orchestrator(
        invoker,
        circuit,
        evaluator=fail_evaluator,
    ).execute(TencentShadowInput(SECURITIES, ()))
    assert result.status is RouteTerminalStatus.FAILED
    assert circuit.failures[0][1] is CircuitOutcomeOrigin.EVALUATOR

    for signal in (KeyboardInterrupt(), SystemExit()):
        with pytest.raises(type(signal)):
            _orchestrator(OutcomeInvoker(signal)).execute(
                TencentShadowInput(SECURITIES, ())
            )


def test_no_security_and_call_storage_failure_do_not_invoke_provider() -> None:
    for shadow_input, persistence in [
        (TencentShadowInput((), ()), FakePersistence()),
        (TencentShadowInput(SECURITIES, ()), FakePersistence("start_call")),
    ]:
        invoker = OutcomeInvoker(AssertionError("must not call"))
        result = _orchestrator(
            invoker,
            persistence=persistence,
        ).execute(shadow_input)
        assert result.status is RouteTerminalStatus.SKIPPED
        assert result.call_budget_used == 0
        assert invoker.calls == 0
