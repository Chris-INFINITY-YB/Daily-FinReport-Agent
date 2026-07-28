from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from daily_report_agent import main
from daily_report_agent.config import (
    ProviderRoutingSettings,
    parse_provider_routing_settings,
)
from daily_report_agent.providers.routing import (
    DataRouteMode,
    RouteContractError,
    RouteErrorCode,
)


ROOT = Path(__file__).parents[2]
ONLINE_MODULE = "daily_report_agent.providers.tencent.online_transport"


@pytest.mark.parametrize("config", [{}, {"pipeline": {}}, {"pipeline": None}])
def test_missing_route_config_defaults_to_legacy(config: dict) -> None:
    assert parse_provider_routing_settings(config) == ProviderRoutingSettings()


@pytest.mark.parametrize(
    ("raw_mode", "mode"),
    [
        ("legacy", DataRouteMode.LEGACY),
        ("provider_shadow", DataRouteMode.PROVIDER_SHADOW),
        ("provider_primary", DataRouteMode.PROVIDER_PRIMARY),
    ],
)
def test_all_three_closed_route_modes_parse(
    raw_mode: str,
    mode: DataRouteMode,
) -> None:
    settings = parse_provider_routing_settings(
        {"pipeline": {"data_route": raw_mode}}
    )

    assert settings.mode is mode


@pytest.mark.parametrize(
    "value",
    ["Legacy", "shadow", "", None, True, 1, [], {}],
)
def test_invalid_route_mode_or_type_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="data_route"):
        parse_provider_routing_settings(
            {"pipeline": {"data_route": value}}
        )


@pytest.mark.parametrize("value", [0, 1, "false", None, []])
def test_invalid_fallback_boolean_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="布尔值"):
        parse_provider_routing_settings(
            {"pipeline": {"fallback_to_legacy": value}}
        )


@pytest.mark.parametrize("value", [-1, 1_000_001, True, 1.5, "10", None])
def test_invalid_provider_priority_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="provider_priorities"):
        parse_provider_routing_settings(
            {"pipeline": {"provider_priorities": {"provider-a": value}}}
        )


@pytest.mark.parametrize(
    "pipeline",
    [
        {"unknown": True},
        {"endpoint": "https://example.invalid"},
        {"api_key": "secret"},
    ],
)
def test_unknown_pipeline_fields_are_rejected(pipeline: dict) -> None:
    with pytest.raises(ValueError, match="不支持"):
        parse_provider_routing_settings({"pipeline": pipeline})


def test_priorities_are_parsed_in_deterministic_order() -> None:
    settings = parse_provider_routing_settings(
        {
            "pipeline": {
                "provider_priorities": {
                    "provider-z": 20,
                    "provider-b": 10,
                    "provider-a": 10,
                }
            }
        }
    )

    assert settings.provider_priorities == (
        ("provider-a", 10),
        ("provider-b", 10),
        ("provider-z", 20),
    )
    assert settings.candidate_provider_ids == (
        "provider-a",
        "provider-b",
        "provider-z",
    )


def test_repository_default_config_preserves_all_closed_defaults() -> None:
    config_path = ROOT / "daily_report_agent" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    settings = parse_provider_routing_settings(config)

    assert settings.mode is DataRouteMode.LEGACY
    assert config["storage"]["enabled"] is False
    assert config["providers"]["tencent_quote"]["shadow_enabled"] is False


@pytest.mark.parametrize("mode", ["provider_shadow", "provider_primary"])
def test_nonlegacy_main_mode_fails_before_any_side_effect(
    mode: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "must-not-exist.sqlite"
    reports = tmp_path / "reports"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
pipeline:
  data_route: {mode}
  fallback_to_legacy: true
watchlist:
  - {{market: cn, symbol: '600519', name: 测试}}
storage:
  enabled: true
  path: {database}
providers:
  tencent_quote:
    shadow_enabled: true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        main,
        "load_env",
        lambda: pytest.fail("不得读取 .env"),
    )
    monkeypatch.setattr(
        main,
        "start_run_context",
        lambda *args, **kwargs: pytest.fail("不得启动存储上下文"),
    )
    monkeypatch.setattr(
        main,
        "maybe_run_tencent_quote_shadow",
        lambda *args, **kwargs: pytest.fail("不得启动腾讯 Shadow"),
    )
    monkeypatch.setattr(
        main,
        "get_source",
        lambda *args, **kwargs: pytest.fail("不得调用旧数据源"),
    )
    monkeypatch.setattr(
        main,
        "build_llm",
        lambda *args, **kwargs: pytest.fail("不得构造 LLM"),
    )
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(reports))

    with pytest.raises(RouteContractError) as raised:
        main.run(str(config), do_notify=False, dry_run=False)

    assert raised.value.code is RouteErrorCode.ROUTE_STAGE_NOT_ENABLED
    assert not database.exists()
    assert not reports.exists()


def test_dry_run_legacy_route_does_not_load_online_transport(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    config = tmp_path / "config.yaml"
    config.write_text(
        """
pipeline:
  data_route: legacy
watchlist:
  - {market: cn, symbol: '600519', name: 测试}
providers:
  tencent_quote:
    shadow_enabled: true
storage:
  enabled: true
  path: should-not-exist.sqlite
""".strip(),
        encoding="utf-8",
    )
    code = f"""
import sys
from daily_report_agent import main
main.report.REPORTS_DIR = {str(reports)!r}
assert main.run({str(config)!r}, do_notify=True, dry_run=True)
assert {ONLINE_MODULE!r} not in sys.modules
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
    assert not (tmp_path / "should-not-exist.sqlite").exists()
