from __future__ import annotations

import ast
import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[4]
SCRIPT = ROOT / "scripts" / "eastmoney_profile_offline_check.py"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "providers"
    / "eastmoney"
    / "profile_synthetic_minimal.json"
)
SYNTHETIC_NAME = "SYNTHETIC_TEST_COMPANY"
SYNTHETIC_INDUSTRY = "SYNTHETIC_TEST_INDUSTRY"


def _module():
    spec = importlib.util.spec_from_file_location(
        "eastmoney_profile_offline_check_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run(module, fixture: Path, capsys, *, symbol: str = "600519"):
    exit_code = module.main(
        ["--fixture", str(fixture), "--symbol", symbol, "--name", "Hidden Name"]
    )
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_synthetic_fixture_success_uses_real_provider_and_fixture_transport(
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    real_provider = module.EastmoneyProfileProvider
    calls: list[object] = []

    class SpyProvider(real_provider):
        def fetch_profile(self, security):
            calls.append(self._transport)
            return super().fetch_profile(security)

    monkeypatch.setattr(module, "EastmoneyProfileProvider", SpyProvider)
    exit_code, output, error = _run(module, FIXTURE, capsys)

    assert exit_code == 0
    assert error == ""
    assert len(calls) == 1
    assert isinstance(calls[0], module.FixtureProfileTransport)
    assert calls[0].calls == ["600519"]
    assert "fixture_kind: synthetic" in output
    assert "provider_id: eastmoney" in output
    assert "executed: true" in output
    assert "item_count: 1" in output
    assert "issue_count: 0" in output
    assert "present_fields: symbol,market,name,industry,source,fetched_at" in output
    assert SYNTHETIC_NAME not in output
    assert SYNTHETIC_INDUSTRY not in output


def test_partial_fixture_succeeds_with_safe_issue_code(
    tmp_path: Path,
    capsys,
) -> None:
    module = _module()
    fixture = _write_fixture(
        tmp_path / "partial.json",
        [{"item": "股票简称", "value": SYNTHETIC_NAME}],
    )

    exit_code, output, error = _run(module, fixture, capsys)

    assert exit_code == 0
    assert error == ""
    assert "item_count: 1" in output
    assert "issue_count: 1" in output
    assert "issue_codes: missing_profile_fields" in output
    assert SYNTHETIC_NAME not in output


def test_empty_list_is_successful_empty_result(tmp_path: Path, capsys) -> None:
    module = _module()
    fixture = _write_fixture(tmp_path / "empty.json", [])

    exit_code, output, error = _run(module, fixture, capsys)

    assert exit_code == 0
    assert error == ""
    assert "item_count: 0" in output
    assert "issue_codes: profile_not_found" in output
    assert "present_fields: none" in output


@pytest.mark.parametrize(
    ("content", "category"),
    [
        ("not-json", "fixture_invalid_json"),
        (json.dumps({"item": "股票简称"}), "fixture_top_level_not_list"),
        (json.dumps(["not-an-object"]), "fixture_row_not_object"),
    ],
)
def test_invalid_fixture_shapes_are_rejected_safely(
    tmp_path: Path,
    capsys,
    content: str,
    category: str,
) -> None:
    module = _module()
    fixture = tmp_path / "invalid.json"
    fixture.write_text(content, encoding="utf-8")

    exit_code, output, error = _run(module, fixture, capsys)

    assert exit_code == 2
    assert output == ""
    assert f"category={category}" in error
    assert content not in error
    assert str(fixture) not in error


def test_nonempty_parser_rejected_structure_is_provider_failure(
    tmp_path: Path,
    capsys,
) -> None:
    module = _module()
    fixture = _write_fixture(
        tmp_path / "malformed.json",
        [{"unexpected": "https://unsafe.invalid/?token=secret"}],
    )

    exit_code, output, error = _run(module, fixture, capsys)

    assert exit_code == 1
    assert output == ""
    assert "category=provider_failure code=invalid_profile_response" in error
    assert "unsafe.invalid" not in error
    assert "token" not in error.lower()


def test_invalid_symbol_is_rejected_by_provider_before_transport(
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    called = False
    original = module.FixtureProfileTransport.fetch_profile_rows

    def track(self, symbol):
        nonlocal called
        called = True
        return original(self, symbol)

    monkeypatch.setattr(module.FixtureProfileTransport, "fetch_profile_rows", track)
    exit_code, output, error = _run(
        module,
        FIXTURE,
        capsys,
        symbol="ABC123",
    )

    assert exit_code == 2
    assert output == ""
    assert "category=invalid_security code=invalid_symbol" in error
    assert called is False


def test_missing_url_directory_and_stdin_fixture_locations_are_rejected(
    tmp_path: Path,
    capsys,
) -> None:
    module = _module()
    cases = [
        (str(tmp_path / "missing.json"), "fixture_missing"),
        ("https://unsafe.invalid/profile.json", "fixture_location_rejected"),
        (str(tmp_path), "fixture_not_regular_file"),
        ("-", "fixture_location_rejected"),
    ]

    for value, category in cases:
        exit_code = module.main(["--fixture", value, "--symbol", "600519"])
        captured = capsys.readouterr()
        assert exit_code == 2
        assert captured.out == ""
        assert f"category={category}" in captured.err
        assert value not in captured.err


def test_output_never_reveals_fixture_values_url_or_token(
    tmp_path: Path,
    capsys,
) -> None:
    module = _module()
    fixture = _write_fixture(
        tmp_path / "sensitive.json",
        [
            {"item": "股票简称", "value": "https://unsafe.invalid/?token=secret"},
            {"item": "行业", "value": "SECRET_INDUSTRY_VALUE"},
        ],
    )

    exit_code, output, error = _run(module, fixture, capsys)

    assert exit_code == 0
    assert error == ""
    assert "provider_id: eastmoney" in output
    for forbidden in ("unsafe.invalid", "token", "secret", "SECRET_INDUSTRY_VALUE"):
        assert forbidden.lower() not in output.lower()


def test_import_and_run_do_not_load_online_clients_or_open_network(
    monkeypatch,
    capsys,
) -> None:
    for module_name in ("akshare", "pandas", "requests"):
        monkeypatch.setitem(sys.modules, module_name, None)
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("offline check must not open a socket")
        ),
    )

    module = _module()
    exit_code, output, error = _run(module, FIXTURE, capsys)

    assert exit_code == 0
    assert error == ""
    assert "executed: true" in output
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported_roots = {
        imported.split(".", 1)[0]
        for node in ast.walk(tree)
        for imported in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    }
    assert imported_roots.isdisjoint({"akshare", "pandas", "requests"})
    assert "--allow-network" not in SCRIPT.read_text(encoding="utf-8")


def test_run_creates_no_database_report_or_other_files(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    monkeypatch.chdir(tmp_path)

    exit_code, output, error = _run(module, FIXTURE, capsys)

    assert exit_code == 0
    assert error == ""
    assert "executed: true" in output
    assert list(tmp_path.iterdir()) == []
    source = SCRIPT.read_text(encoding="utf-8").lower()
    for forbidden in (
        "config.yaml",
        "daily_report_agent.config",
        "daily_report_agent.datasource",
        "daily_report_agent.pipeline",
        "daily_report_agent.storage",
        "daily_report_agent.analyzer",
        "daily_report_agent.report",
        "daily_report_agent.notifier",
        ".env",
        "load_env",
        "watchlist",
        "sqlite",
        "database",
        "llm",
    ):
        assert forbidden not in source
