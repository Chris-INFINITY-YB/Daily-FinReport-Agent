from __future__ import annotations

import importlib
import socket
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from daily_report_agent.providers.contracts import ProviderCapability
from daily_report_agent.providers.tencent.constants import TENCENT_QUOTE_DESCRIPTOR
from daily_report_agent.providers.tencent.quote import TencentQuoteProvider


ROOT = Path(__file__).parents[4]


def test_tencent_descriptor_is_stable_quote_only_cn() -> None:
    descriptor = TENCENT_QUOTE_DESCRIPTOR
    assert descriptor.provider_id == "tencent-finance"
    assert descriptor.display_name == "Tencent Finance"
    assert descriptor.capabilities == frozenset({ProviderCapability.QUOTE})
    assert descriptor.markets == frozenset({"cn"})
    assert descriptor.version is None
    assert descriptor.supported_news_types == frozenset()
    assert TencentQuoteProvider.descriptor is descriptor
    with pytest.raises(FrozenInstanceError):
        descriptor.version = "fake"  # type: ignore[misc]


def test_tencent_modules_have_no_online_or_price_window_dependencies() -> None:
    source_dir = ROOT / "daily_report_agent" / "providers" / "tencent"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_dir.glob("*.py"))
    assert "import requests" not in source
    assert "from requests" not in source
    assert "import httpx" not in source
    assert "from httpx" not in source
    assert "PriceWindow" not in source
    assert "provider_calls" not in source
    assert "Database" not in source


def test_import_and_construction_do_not_access_network(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("Tencent module import must remain offline")

    monkeypatch.setattr(socket, "socket", fail)
    import daily_report_agent.providers.tencent as tencent_module

    reloaded = importlib.reload(tencent_module)

    class Transport:
        def fetch_quote_text(self, symbols, *, timeout_seconds):
            raise AssertionError("construction must not call transport")

    provider = reloaded.TencentQuoteProvider(Transport())
    assert provider.descriptor.provider_id == "tencent-finance"


def test_production_entry_points_do_not_import_tencent_provider() -> None:
    protected = [
        ROOT / "daily_report_agent" / "main.py",
        ROOT / "daily_report_agent" / "config.yaml",
        ROOT / "daily_report_agent" / "datasource" / "cn.py",
        ROOT / "daily_report_agent" / "analyzer.py",
        ROOT / "daily_report_agent" / "report.py",
        ROOT / "daily_report_agent" / "pipeline" / "runner.py",
    ]
    for path in protected:
        content = path.read_text(encoding="utf-8").lower()
        assert "tencentquoteprovider" not in content
        assert "tencentonlinequotetransport" not in content
        assert "tencent-finance" not in content
