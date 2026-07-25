from __future__ import annotations

import importlib
import socket
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from typing import get_type_hints

import pytest

from daily_report_agent.models.security import Security
from daily_report_agent.providers.contracts import ProviderCapability
from daily_report_agent.providers.eastmoney.constants import (
    EASTMONEY_NEWS_DESCRIPTOR,
    EASTMONEY_PROFILE_DESCRIPTOR,
)
from daily_report_agent.providers.eastmoney.news import EastmoneyNewsProvider
from daily_report_agent.providers.eastmoney.news_transport import (
    EastmoneyNewsTransport,
)


ROOT = Path(__file__).parents[4]
NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


class NoCallTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_news_rows(self, symbol: str):
        self.calls.append(symbol)
        raise AssertionError("construction must not call transport")


def test_news_descriptor_is_stable_news_only_cn() -> None:
    descriptor = EASTMONEY_NEWS_DESCRIPTOR

    assert descriptor.provider_id == "eastmoney"
    assert descriptor.display_name == "Eastmoney"
    assert descriptor.capabilities == frozenset({ProviderCapability.NEWS})
    assert descriptor.markets == frozenset({"cn"})
    assert descriptor.version is None
    assert descriptor.supported_news_types == frozenset({"news"})
    assert EastmoneyNewsProvider.descriptor is descriptor
    with pytest.raises(FrozenInstanceError):
        descriptor.version = "changed"  # type: ignore[misc]


def test_profile_descriptor_remains_profile_only() -> None:
    assert EASTMONEY_PROFILE_DESCRIPTOR.capabilities == frozenset(
        {ProviderCapability.PROFILE}
    )
    assert EASTMONEY_PROFILE_DESCRIPTOR.supported_news_types == frozenset()


def test_news_transport_is_read_only_rows_protocol() -> None:
    annotations = get_type_hints(EastmoneyNewsTransport.fetch_news_rows)

    assert EastmoneyNewsTransport._is_protocol is True
    assert annotations["symbol"] is str
    assert annotations["return"] == tuple[Mapping[str, object], ...]


def test_news_package_exports_are_importable() -> None:
    from daily_report_agent.providers import (
        EASTMONEY_NEWS_DESCRIPTOR as top_descriptor,
    )
    from daily_report_agent.providers import EastmoneyNewsProvider as top_provider
    from daily_report_agent.providers.eastmoney import (
        EastmoneyNewsTransport as package_transport,
    )

    assert top_descriptor is EASTMONEY_NEWS_DESCRIPTOR
    assert top_provider is EastmoneyNewsProvider
    assert package_transport is EastmoneyNewsTransport


def test_import_and_construction_do_not_access_network(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("news import and construction must remain offline")

    monkeypatch.setattr(socket, "socket", fail)
    import daily_report_agent.providers.eastmoney as eastmoney_module

    reloaded = importlib.reload(eastmoney_module)
    transport = NoCallTransport()
    provider = reloaded.EastmoneyNewsProvider(transport, clock=lambda: NOW)

    assert provider.descriptor.provider_id == "eastmoney"
    assert transport.calls == []


def test_no_default_online_transport_exists() -> None:
    with pytest.raises(TypeError):
        EastmoneyNewsProvider()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="transport"):
        EastmoneyNewsProvider(None)  # type: ignore[arg-type]


def test_production_entry_points_do_not_import_news_provider() -> None:
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
        assert "eastmoneynewsprovider" not in content
        assert "eastmoney_news_descriptor" not in content


def test_provider_has_news_protocol_method_shape() -> None:
    transport = type("Transport", (), {"fetch_news_rows": lambda self, symbol: ()})()
    result = EastmoneyNewsProvider(transport, clock=lambda: NOW).fetch_news(
        Security("cn", "123456", "Synthetic"),
        NOW,
        NOW,
        1,
    )

    assert result.provider is EASTMONEY_NEWS_DESCRIPTOR
    assert result.items == ()
