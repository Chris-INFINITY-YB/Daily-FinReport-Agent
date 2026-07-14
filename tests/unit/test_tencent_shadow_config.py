from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from daily_report_agent.config import (
    TencentQuoteShadowSettings,
    parse_tencent_quote_shadow_settings,
)
from daily_report_agent.pipeline.tencent_quote_shadow import (
    build_request_fingerprint,
    build_shadow_securities,
)
from daily_report_agent.models.security import Security


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"providers": {}},
        {"providers": {"tencent_quote": {}}},
        {"providers": {"tencent_quote": {"shadow_enabled": False}}},
    ],
)
def test_shadow_defaults_to_disabled_for_old_and_explicit_configs(config) -> None:
    assert parse_tencent_quote_shadow_settings(config) == TencentQuoteShadowSettings()


@pytest.mark.parametrize(
    "config",
    [
        {"providers": []},
        {"providers": {"tencent_quote": []}},
    ],
)
def test_shadow_rejects_non_mapping_sections(config) -> None:
    with pytest.raises(ValueError, match="YAML 映射"):
        parse_tencent_quote_shadow_settings(config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shadow_enabled", 1),
        ("timeout_seconds", "10"),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", 0),
        ("timeout_seconds", 61),
        ("batch_size", 0),
        ("batch_size", True),
        ("batch_size", 101),
        ("max_symbols", -1),
        ("max_symbols", 101),
    ],
)
def test_shadow_rejects_invalid_settings(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        parse_tencent_quote_shadow_settings(
            {"providers": {"tencent_quote": {field: value}}}
        )


@pytest.mark.parametrize("field", ["endpoint", "retry_count", "api_key"])
def test_endpoint_retry_and_credentials_cannot_be_configured(field: str) -> None:
    with pytest.raises(ValueError, match="不支持"):
        parse_tencent_quote_shadow_settings(
            {"providers": {"tencent_quote": {field: "unsafe"}}}
        )


def test_explicit_settings_are_parsed_without_environment_or_files() -> None:
    result = parse_tencent_quote_shadow_settings(
        {
            "providers": {
                "tencent_quote": {
                    "shadow_enabled": True,
                    "timeout_seconds": 5,
                    "batch_size": 10,
                    "max_symbols": 3,
                }
            }
        }
    )
    assert result == TencentQuoteShadowSettings(True, 5.0, 10, 3)


def test_cn_watchlist_order_limit_and_name_fallback_are_stable() -> None:
    watchlist = [
        {"market": "us", "symbol": "AAPL", "name": "苹果"},
        {"market": "cn", "symbol": "600519", "name": "贵州茅台"},
        {"market": "cn", "symbol": "000001", "name": ""},
        {"market": "cn", "symbol": "300750", "name": None},
    ]
    securities = build_shadow_securities(watchlist, max_symbols=2)
    assert [(item.symbol, item.name) for item in securities] == [
        ("600519", "贵州茅台"),
        ("000001", "000001"),
    ]
    assert watchlist[2]["name"] == ""


def test_fingerprint_is_sorted_name_independent_and_set_sensitive() -> None:
    first = (
        Security("cn", "600519", "名称一"),
        Security("cn", "000001", "名称二"),
    )
    renamed_reordered = (
        Security("cn", "000001", "已改名"),
        Security("cn", "600519", "另一个名称"),
    )
    changed = (Security("cn", "600519", "名称一"),)
    assert build_request_fingerprint(first) == build_request_fingerprint(renamed_reordered)
    assert build_request_fingerprint(first) != build_request_fingerprint(changed)
    assert "名称" not in build_request_fingerprint(first)


def test_repository_default_config_keeps_shadow_off() -> None:
    config_path = Path(__file__).parents[2] / "daily_report_agent" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    settings = parse_tencent_quote_shadow_settings(config)
    assert settings.enabled is False
    assert "endpoint" not in config["providers"]["tencent_quote"]
