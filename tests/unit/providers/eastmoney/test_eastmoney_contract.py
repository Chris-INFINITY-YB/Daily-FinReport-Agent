from __future__ import annotations

import importlib
import socket
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints

import pytest

from daily_report_agent.providers.contracts import ProviderCapability
from daily_report_agent.providers.eastmoney.constants import (
    EASTMONEY_PROFILE_DESCRIPTOR,
)
from daily_report_agent.providers.eastmoney.profile import (
    EastmoneyProfileProvider,
)
from daily_report_agent.providers.eastmoney.transport import (
    EastmoneyProfileTransport,
)


ROOT = Path(__file__).parents[4]


class NoCallTransport:
    def fetch_profile_rows(self, symbol: str):
        raise AssertionError("construction must not call transport")


def test_descriptor_is_stable_profile_only_cn() -> None:
    descriptor = EASTMONEY_PROFILE_DESCRIPTOR

    assert descriptor.provider_id == "eastmoney"
    assert descriptor.display_name == "Eastmoney"
    assert descriptor.capabilities == frozenset({ProviderCapability.PROFILE})
    assert descriptor.markets == frozenset({"cn"})
    assert descriptor.version is None
    assert descriptor.supported_news_types == frozenset()
    assert EastmoneyProfileProvider.descriptor is descriptor
    with pytest.raises(FrozenInstanceError):
        descriptor.version = "akshare-version"  # type: ignore[misc]


def test_transport_is_a_read_only_rows_protocol() -> None:
    annotations = get_type_hints(EastmoneyProfileTransport.fetch_profile_rows)

    assert EastmoneyProfileTransport._is_protocol is True
    assert annotations["symbol"] is str
    assert annotations["return"] == tuple[Mapping[str, object], ...]


def test_modules_have_no_online_or_production_dependencies() -> None:
    source_dir = ROOT / "daily_report_agent" / "providers" / "eastmoney"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in source_dir.glob("*.py")
    ).lower()

    for forbidden in (
        "import akshare",
        "from akshare",
        "import pandas",
        "from pandas",
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import urllib",
        "from urllib",
        "dataframe",
        "database",
        "config.yaml",
        "daily_report_agent.analyzer",
        "daily_report_agent.report",
        "daily_report_agent.notifier",
    ):
        assert forbidden not in source


def test_import_and_construction_do_not_access_network(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("Eastmoney import and construction must remain offline")

    monkeypatch.setattr(socket, "socket", fail)
    import daily_report_agent.providers.eastmoney as eastmoney_module

    reloaded = importlib.reload(eastmoney_module)
    provider = reloaded.EastmoneyProfileProvider(NoCallTransport())

    assert provider.descriptor.provider_id == "eastmoney"


def test_production_entry_points_do_not_import_eastmoney_provider() -> None:
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
        assert "eastmoneyprofileprovider" not in content
        assert "eastmoney_profile_descriptor" not in content
