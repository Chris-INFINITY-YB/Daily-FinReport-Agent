from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "tencent_quote_shadow_observe.py"
SUMMARY_SCRIPT = ROOT / "scripts" / "tencent_quote_shadow_summary.py"
FIXTURES = ROOT / "tests" / "fixtures" / "providers" / "tencent"
WHEN = datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _module():
    return _load(SCRIPT, "tencent_quote_shadow_observe_test")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FixtureTransport:
    def __init__(self, outcome: str | BaseException) -> None:
        self.outcome = outcome
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def fetch_quote_text(self, symbols, *, timeout_seconds):
        self.calls.append((symbols, timeout_seconds))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _observe(module, database_path: Path, symbols: list[str], outcome, run_id: str):
    transport = FixtureTransport(outcome)
    result = module.observe_once(
        database_path=database_path,
        securities=module._parse_symbols(symbols),
        timeout_seconds=5.0,
        transport_factory=lambda: transport,
        clock=lambda: WHEN,
        monotonic=lambda: 10.0,
        run_id_factory=lambda: run_id,
    )
    return result, transport


def _count(database_path: Path, table: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_cli_without_network_permission_stops_before_observation_or_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    database_path = tmp_path / "must-not-exist.sqlite3"

    def fail(**kwargs):
        raise AssertionError("observation and transport construction must not run")

    monkeypatch.setattr(module, "observe_once", fail)
    exit_code = module.main(
        [
            "--db-path",
            str(database_path),
            "--symbols",
            "600519",
            "300750",
            "000001",
        ]
    )

    assert exit_code == 2
    assert not database_path.exists()
    assert "--allow-network" in capsys.readouterr().err


def test_invalid_or_mixed_symbols_stop_before_observation_and_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    database_path = tmp_path / "invalid.sqlite3"

    def fail(**kwargs):
        raise AssertionError("invalid symbols must not reach observation")

    monkeypatch.setattr(module, "observe_once", fail)
    exit_code = module.main(
        [
            "--allow-network",
            "--db-path",
            str(database_path),
            "--symbols",
            "600519",
            "ABC123",
        ]
    )

    assert exit_code == 2
    assert not database_path.exists()
    assert "rejected" in capsys.readouterr().err


def test_missing_symbols_argument_fails_before_database_creation(
    tmp_path: Path,
) -> None:
    module = _module()
    database_path = tmp_path / "empty.sqlite3"

    try:
        module.main(
            [
                "--allow-network",
                "--db-path",
                str(database_path),
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("argparse must reject a missing --symbols argument")

    assert not database_path.exists()


def test_formal_default_database_path_is_explicitly_rejected(
    monkeypatch,
    capsys,
) -> None:
    module = _module()

    def fail(**kwargs):
        raise AssertionError("formal database path must not reach observation")

    monkeypatch.setattr(module, "observe_once", fail)
    exit_code = module.main(
        [
            "--allow-network",
            "--db-path",
            str(module.FORMAL_DEFAULT_DATABASE),
            "--symbols",
            "600519",
        ]
    )

    assert exit_code == 2
    assert "formal default database" in capsys.readouterr().err


def test_duplicate_symbols_are_deduplicated_in_stable_order() -> None:
    module = _module()
    securities = module._parse_symbols(
        ["600519", "300750", "600519", "000001", "300750"]
    )
    assert tuple(item.symbol for item in securities) == (
        "600519",
        "300750",
        "000001",
    )


def test_fixture_success_calls_once_persists_expected_rows_and_is_summary_compatible(
    tmp_path: Path,
    capsys,
) -> None:
    module = _module()
    database_path = tmp_path / "new-parent" / "observation.sqlite3"
    result, transport = _observe(
        module,
        database_path,
        ["600000", "300750"],
        _fixture("quote_batch_mixed.txt"),
        "observe-success",
    )

    assert result.success is True
    assert result.status == "success"
    assert result.requested_count == result.received_count == 2
    assert result.missing_symbols == ()
    assert result.logical_calls == 1
    assert result.http_requests == 1
    assert transport.calls == [(('sh600000', 'sz300750'), 5.0)]
    assert result.created_provider_calls == 1
    assert result.created_snapshots == 2
    assert result.created_raw_responses == 0
    assert result.issue_count == 0
    assert result.provider_call_id is not None
    assert _count(database_path, "pipeline_runs") == 1
    assert _count(database_path, "provider_calls") == 1
    assert _count(database_path, "market_snapshots") == 2
    assert _count(database_path, "raw_responses") == 0

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT status FROM pipeline_runs WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0] == "success"

    capsys.readouterr()
    summary_module = _load(SUMMARY_SCRIPT, "tencent_quote_shadow_summary_observe_test")
    assert summary_module.main(["--database", str(database_path), "--days", "7"]) == 0
    output = capsys.readouterr().out
    assert "logical_calls: 1" in output
    assert "tencent_snapshot_rows: 2" in output


def test_missing_symbol_is_reported_while_returned_snapshot_is_persisted(
    tmp_path: Path,
) -> None:
    module = _module()
    database_path = tmp_path / "partial.sqlite3"
    result, transport = _observe(
        module,
        database_path,
        ["600000", "300750"],
        _fixture("quote_single_sh.txt"),
        "observe-missing",
    )

    assert result.success is True
    assert result.received_count == 1
    assert result.missing_symbols == ("300750",)
    assert result.issue_count == 1
    assert result.issue_codes == ("missing_requested_symbol",)
    assert result.created_snapshots == 1
    assert result.created_provider_calls == 1
    assert result.created_raw_responses == 0
    assert result.logical_calls == 1
    assert result.http_requests == 1
    assert len(transport.calls) == 1


def test_parser_failure_is_traced_without_fabricated_snapshot(tmp_path: Path) -> None:
    module = _module()
    database_path = tmp_path / "parser-error.sqlite3"
    result, transport = _observe(
        module,
        database_path,
        ["000001"],
        _fixture("quote_all_malformed.txt"),
        "observe-parser-error",
    )

    assert result.success is False
    assert result.status == "failed"
    assert result.received_count == 0
    assert result.missing_symbols == ("000001",)
    assert result.issue_count == 1
    assert result.issue_codes == ("no_valid_records",)
    assert result.created_snapshots == 0
    assert result.created_provider_calls == 1
    assert result.created_raw_responses == 0
    assert result.logical_calls == 1
    assert result.http_requests == 1
    assert len(transport.calls) == 1
    assert _count(database_path, "market_snapshots") == 0

    with sqlite3.connect(database_path) as connection:
        call = connection.execute(
            "SELECT status, error_category, error_code FROM provider_calls"
        ).fetchone()
    assert call == ("failed", "parse", "no_valid_records")


def test_provider_error_is_traced_without_snapshot_or_retry(tmp_path: Path) -> None:
    module = _module()
    database_path = tmp_path / "provider-error.sqlite3"
    result, transport = _observe(
        module,
        database_path,
        ["600000"],
        TimeoutError("unsafe internal timeout details"),
        "observe-provider-error",
    )

    assert result.status == "failed"
    assert result.issue_codes == ("transport_timeout",)
    assert result.created_snapshots == 0
    assert result.created_provider_calls == 1
    assert result.created_raw_responses == 0
    assert result.logical_calls == 1
    assert result.http_requests == 1
    assert len(transport.calls) == 1

    with sqlite3.connect(database_path) as connection:
        call = connection.execute(
            "SELECT status, error_category, error_code, retry_count FROM provider_calls"
        ).fetchone()
    assert call == ("failed", "network", "transport_timeout", 0)


def test_repeated_run_reuses_database_and_keeps_snapshot_idempotency(
    tmp_path: Path,
) -> None:
    module = _module()
    database_path = tmp_path / "repeat.sqlite3"
    first, first_transport = _observe(
        module,
        database_path,
        ["600000", "300750"],
        _fixture("quote_batch_mixed.txt"),
        "observe-repeat-1",
    )
    second, second_transport = _observe(
        module,
        database_path,
        ["600000", "300750"],
        _fixture("quote_batch_mixed.txt"),
        "observe-repeat-2",
    )

    assert first.created_snapshots == 2
    assert second.created_snapshots == 0
    assert first.created_provider_calls == second.created_provider_calls == 1
    assert first.created_raw_responses == second.created_raw_responses == 0
    assert len(first_transport.calls) == len(second_transport.calls) == 1
    assert _count(database_path, "pipeline_runs") == 2
    assert _count(database_path, "provider_calls") == 2
    assert _count(database_path, "market_snapshots") == 2
    assert _count(database_path, "raw_responses") == 0


def test_observation_script_has_no_formal_pipeline_dependencies() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "config.yaml" not in source
    assert "daily_report_agent.main" not in source
    assert "daily_report_agent.analyzer" not in source
    assert "daily_report_agent.report" not in source
    assert "daily_report_agent.notifier" not in source
    assert "get_source" not in source
    assert "load_env" not in source
    assert "insert_redacted_response" not in source
