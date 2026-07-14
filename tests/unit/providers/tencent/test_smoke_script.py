from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

from daily_report_agent.providers.tencent.online_transport import (
    OnlineResponseMetadata,
)


ROOT = Path(__file__).parents[4]
SCRIPT_PATH = ROOT / "scripts" / "tencent_quote_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("tencent_quote_smoke_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_smoke_refuses_to_run_without_explicit_network_flag(
    monkeypatch,
    capsys,
) -> None:
    module = _load_smoke_module()

    def fail(*args, **kwargs):
        raise AssertionError("transport must not be constructed")

    monkeypatch.setattr(module, "TencentOnlineQuoteTransport", fail)
    assert module.main([]) == 2
    assert "--allow-network" in capsys.readouterr().err


def test_smoke_rejects_more_than_three_symbols_before_network(
    monkeypatch,
    capsys,
) -> None:
    module = _load_smoke_module()

    def fail(*args, **kwargs):
        raise AssertionError("transport must not be constructed")

    monkeypatch.setattr(module, "TencentOnlineQuoteTransport", fail)
    exit_code = module.main(
        ["--allow-network", "--symbols", "600000,000001,300750,002594"]
    )
    assert exit_code == 1
    assert "one and three" in capsys.readouterr().err


def test_allowed_smoke_uses_one_fake_request_and_prints_redacted_summary(
    monkeypatch,
    capsys,
) -> None:
    module = _load_smoke_module()
    text = (
        'v_sh600000="1~浦发银行~600000~10.25~10.00'
        '~~~~~~~~~~~~~~~~~~~~~~~~~~20260714150000~~2.50~~~~~~~~~~~~~~~~";'
    )

    class FakeOnlineTransport:
        def __init__(self):
            self.request_count = 0
            self.last_response_metadata = None

        def fetch_quote_text(self, symbols, *, timeout_seconds):
            self.request_count += 1
            self.last_response_metadata = OnlineResponseMetadata(
                http_status=200,
                content_type="text/plain; charset=GBK",
                encoding="gbk",
                response_bytes=len(text.encode("gbk")),
                record_symbols=("sh600000",),
                record_statuses=("1",),
                field_counts=(49,),
            )
            return text

    monkeypatch.setattr(module, "TencentOnlineQuoteTransport", FakeOnlineTransport)
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary smoke unit test must not open a socket")
        ),
    )

    exit_code = module.main(
        ["--allow-network", "--symbols", "600000", "--timeout", "2"]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "provider: tencent-finance" in output
    assert "requested: 1" in output
    assert "received: 1" in output
    assert "requests: 1" in output
    assert "http_status: 200" in output
    assert "pct_cross_check: true" in output
    assert "v_sh600000" not in output


def test_smoke_script_has_no_production_or_persistence_dependencies() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "config.yaml" not in source
    assert "get_source" not in source
    assert "Database" not in source
    assert "provider_calls" not in source
    assert "Analyzer" not in source
    assert "notify" not in source
