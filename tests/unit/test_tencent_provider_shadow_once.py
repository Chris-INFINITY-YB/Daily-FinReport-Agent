from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from daily_report_agent.pipeline.tencent_provider_shadow import (
    TencentProviderShadowResult,
)
from daily_report_agent.providers.routing import RouteTerminalStatus
from scripts import tencent_provider_shadow_once as once


ROOT = Path(__file__).parents[2]
ONLINE_MODULE = "daily_report_agent.providers.tencent.online_transport"


def _run_module(*args: str, import_trace: bool = False):
    command = [sys.executable]
    if import_trace:
        command.extend(["-X", "importtime"])
    command.extend(["-m", "scripts.tencent_provider_shadow_once", *args])
    return subprocess.run(
        command,
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_database_family_absent(database: Path) -> None:
    assert not database.exists()
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def _assert_online_boundaries_not_loaded(stderr: str) -> None:
    assert ONLINE_MODULE not in stderr
    assert "daily_report_agent.storage.database" not in stderr
    assert not any(
        line.rstrip().endswith((" requests", "| requests"))
        for line in stderr.splitlines()
    )


def test_module_entrypoint_missing_gate_refuses_before_all_side_effects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "must-not-exist.sqlite"
    completed = _run_module(
        "--database",
        str(database),
        import_trace=True,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.rstrip().endswith(
        "refused: --allow-network-once is required"
    )
    _assert_online_boundaries_not_loaded(completed.stderr)
    _assert_database_family_absent(database)


def test_module_entrypoint_help_is_offline_and_exits_zero(tmp_path: Path) -> None:
    database = tmp_path / "must-not-exist.sqlite"
    completed = _run_module("--help", import_trace=True)
    assert completed.returncode == 0
    assert "--allow-network-once" in completed.stdout
    assert "--database" in completed.stdout
    _assert_online_boundaries_not_loaded(completed.stderr)
    _assert_database_family_absent(database)


def test_module_entrypoint_invalid_values_refuse_before_online_boundaries(
    tmp_path: Path,
) -> None:
    sensitive = "https://user:token@example.test/response-body"
    cases = (
        ("--symbols", sensitive),
        ("--timeout", "10.1"),
    )
    for index, invalid in enumerate(cases):
        database = tmp_path / f"invalid-{index}.sqlite"
        completed = _run_module(
            "--allow-network-once",
            "--database",
            str(database),
            *invalid,
            import_trace=True,
        )
        assert completed.returncode == 2
        assert completed.stdout == ""
        assert completed.stderr.rstrip().endswith(
            (
                "refused: M1-05B 证券集合或顺序不符合固定门禁"
                if invalid[0] == "--symbols"
                else "refused: timeout 必须大于 0 且不超过 10 秒"
            )
        )
        refusal = completed.stderr.splitlines()[-1]
        assert str(database) not in refusal
        assert sensitive not in refusal
        assert "token" not in refusal
        assert "response-body" not in refusal
        _assert_online_boundaries_not_loaded(completed.stderr)
        _assert_database_family_absent(database)


def test_missing_network_gate_refuses_before_database_or_online_import(
    tmp_path: Path,
) -> None:
    database = tmp_path / "must-not-exist.sqlite"
    code = f"""
import sys
from scripts.tencent_provider_shadow_once import run_once
calls = 0
def unexpected_executor(**kwargs):
    global calls
    calls += 1
    raise AssertionError('logical Provider call must remain zero')
result = run_once(
    ['--database', {str(database)!r}],
    executor=unexpected_executor,
)
assert result == 2
assert calls == 0
assert {ONLINE_MODULE!r} not in sys.modules
assert 'daily_report_agent.storage.database' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    _assert_database_family_absent(database)


def test_fixed_symbols_timeout_and_fresh_external_database_are_required(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing.sqlite"
    existing.touch()
    executor_calls = 0

    def unexpected_executor(**kwargs):
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("logical Provider call must remain zero")

    cases = [
        [
            "--allow-network-once",
            "--database",
            str(tmp_path / "a.sqlite"),
            "--symbols",
            "600519",
        ],
        [
            "--allow-network-once",
            "--database",
            str(tmp_path / "b.sqlite"),
            "--timeout",
            "10.1",
        ],
        [
            "--allow-network-once",
            "--database",
            str(existing),
        ],
        [
            "--allow-network-once",
            "--database",
            str(ROOT / "forbidden.sqlite"),
        ],
    ]
    for argv in cases:
        assert once.run_once(argv, executor=unexpected_executor) == 2
    assert executor_calls == 0


def test_fake_executor_receives_strict_gate_without_loading_online_transport(
    tmp_path: Path,
    capsys,
) -> None:
    captured = []

    def fake_executor(**kwargs):
        captured.append(kwargs)
        return TencentProviderShadowResult(
            RouteTerminalStatus.SUCCESS,
            3,
            3,
            0,
            3,
            1,
            7,
        )

    database = tmp_path / "shadow.sqlite"
    code = once.run_once(
        [
            "--allow-network-once",
            "--database",
            str(database),
            "--symbols",
            "600519,300750,000001",
            "--timeout",
            "10",
        ],
        executor=fake_executor,
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(captured) == 1
    assert captured[0]["gate"].routing.max_call_budget == 1
    assert captured[0]["gate"].quote.max_symbols == 3
    assert [item["symbol"] for item in captured[0]["watchlist"]] == [
        "600519",
        "300750",
        "000001",
    ]
    assert payload == {
        "error_code": None,
        "fallback_count": 0,
        "http_requests": 0,
        "issue_count": 0,
        "item_count": 3,
        "logical_calls": 1,
        "raw_response_count": 0,
        "retry_count": 0,
        "status": "success",
    }
    assert not database.exists()


def test_one_request_wrapper_rejects_second_physical_call() -> None:
    class Transport:
        def __init__(self):
            self.calls = 0

        def fetch_quote_text(self, symbols, *, timeout_seconds):
            self.calls += 1
            return "fixture"

    counter = once._HttpRequestCounter()
    base = Transport()
    transport = once._OneRequestTransport(base, counter)
    assert transport.fetch_quote_text(("sh600519",), timeout_seconds=10) == (
        "fixture"
    )
    try:
        transport.fetch_quote_text(("sz300750",), timeout_seconds=10)
    except RuntimeError:
        pass
    else:
        raise AssertionError("second HTTP call must be rejected")
    assert counter.count == 1
    assert base.calls == 1
