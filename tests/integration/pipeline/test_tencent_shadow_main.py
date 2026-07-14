from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from daily_report_agent import main
from daily_report_agent.datasource.base import StockData
from daily_report_agent.pipeline.tencent_quote_shadow import TencentQuoteShadowResult


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
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
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
        return TencentQuoteShadowResult(
            True, "failed", 1, 0, 0, 1, 7, 10, "provider_failed"
        )

    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "build_llm", lambda cfg: FakeLLM())
    monkeypatch.setattr(main, "get_source", lambda market: Source())
    monkeypatch.setattr(main, "maybe_run_tencent_quote_shadow", failed_shadow)
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))

    report_path = Path(main.run(str(config), do_notify=False, dry_run=False))
    with closing(sqlite3.connect(database)) as connection:
        status = connection.execute("SELECT status FROM pipeline_runs").fetchone()[0]
    assert report_path.exists()
    assert len(calls) == 1
    assert status == "success"
