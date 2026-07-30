from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace
from contextlib import closing
from pathlib import Path

import pytest

from daily_report_agent import main
from daily_report_agent.datasource.base import StockData
from daily_report_agent.pipeline.tencent_provider_shadow import (
    TencentProviderShadowResult,
)
from daily_report_agent.providers.routing import RouteTerminalStatus


class FakeLLM:
    def chat(self, system_prompt, user_prompt):
        return "固定分析结果"


class Source:
    def fetch(self, symbol, name, news_days, max_news):
        return StockData(
            symbol=symbol,
            name=name,
            market="us",
            start_price=100.0,
            end_price=101.0,
            pct_change=1.0,
        )


def test_shadow_failure_result_does_not_change_formal_pipeline_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "agent.sqlite"
    shadow_database = tmp_path / "shadow.sqlite"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
pipeline:
  data_route: provider_shadow
  max_provider_calls: 1
  provider_shadow_database_path: {shadow_database}
watchlist:
  - {{market: us, symbol: TEST, name: 测试标的}}
storage:
  enabled: true
  path: {database}
providers:
  tencent_quote:
    shadow_enabled: true
llm:
  provider: fixture
notify:
  email: false
""".strip(),
        encoding="utf-8",
    )
    calls = []

    def failed_shadow(**kwargs):
        calls.append(kwargs)
        return TencentProviderShadowResult(
            RouteTerminalStatus.FAILED,
            1,
            0,
            1,
            0,
            1,
            7,
            "provider_failed",
        )

    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "build_llm", lambda cfg: FakeLLM())
    monkeypatch.setattr(main, "get_source", lambda market: Source())
    monkeypatch.setattr(
        main,
        "run_tencent_provider_shadow_from_config",
        failed_shadow,
    )
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))

    report_path = Path(
        main.run(
            str(config),
            do_notify=False,
            dry_run=False,
            allow_provider_shadow=True,
        )
    )
    with closing(sqlite3.connect(database)) as connection:
        status = connection.execute("SELECT status FROM pipeline_runs").fetchone()[0]
    assert report_path.exists()
    assert len(calls) == 1
    assert status == "success"


def test_legacy_never_enters_new_shadow_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "legacy.yaml"
    config.write_text(
        """
pipeline:
  data_route: legacy
watchlist:
  - {market: us, symbol: TEST, name: 测试标的}
providers:
  tencent_quote:
    shadow_enabled: true
llm:
  provider: fixture
notify:
  email: false
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "build_llm", lambda cfg: FakeLLM())
    monkeypatch.setattr(main, "get_source", lambda market: Source())
    monkeypatch.setattr(
        main,
        "run_tencent_provider_shadow_from_config",
        lambda **kwargs: pytest.fail("legacy 不得进入 Provider Shadow"),
    )
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))
    assert main.run(str(config), do_notify=False, dry_run=False)


def test_shadow_failure_does_not_change_legacy_report_or_add_notification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy = tmp_path / "legacy.yaml"
    shadow = tmp_path / "shadow.yaml"
    common = """
watchlist:
  - {market: us, symbol: TEST, name: 测试标的}
llm:
  provider: fixture
notify:
  email: true
""".strip()
    legacy.write_text(
        "pipeline:\n  data_route: legacy\n" + common,
        encoding="utf-8",
    )
    shadow.write_text(
        (
            "pipeline:\n"
            "  data_route: provider_shadow\n"
            "  max_provider_calls: 1\n"
            f"  provider_shadow_database_path: {tmp_path / 'shadow.sqlite'}\n"
            "providers:\n"
            "  tencent_quote:\n"
            "    shadow_enabled: true\n"
            f"{common}"
        ),
        encoding="utf-8",
    )
    rendered = []
    notified = []

    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "build_llm", lambda cfg: FakeLLM())
    monkeypatch.setattr(main, "get_source", lambda market: Source())
    monkeypatch.setattr(
        main.report,
        "save",
        lambda content, date: rendered.append(content) or str(tmp_path / "report.md"),
    )
    monkeypatch.setattr(
        main,
        "run_tencent_provider_shadow_from_config",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("unsafe shadow detail")
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "daily_report_agent.notifier",
        SimpleNamespace(
            notify=lambda *args, **kwargs: notified.append((args, kwargs))
        ),
    )

    main.run(str(legacy), do_notify=True, dry_run=False)
    main.run(
        str(shadow),
        do_notify=True,
        dry_run=False,
        allow_provider_shadow=True,
    )
    assert rendered[0] == rendered[1]
    assert len(notified) == 2
