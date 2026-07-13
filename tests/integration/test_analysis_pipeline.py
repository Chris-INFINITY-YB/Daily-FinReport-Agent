from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from daily_report_agent import main
from daily_report_agent.datasource.base import NewsItem, StockData


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return "固定分析结果"


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 13, tzinfo=tz)


def test_production_compatibility_path_reports_missing_market(
    config_path: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    data = StockData(
        symbol="TEST",
        name="测试标的",
        market="us",
        news=[NewsItem(date="2026-07-12", headline="固定新闻")],
        error="行情抓取失败",
    )
    llm = FakeLLM()

    class Source:
        def fetch(self, symbol, name, news_days, max_news):
            return data

    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "build_llm", lambda cfg: llm)
    monkeypatch.setattr(main, "get_source", lambda market: Source())
    monkeypatch.setattr(main, "datetime", FixedDateTime)
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))

    path = Path(main.run(str(config_path), do_notify=False, dry_run=False))
    content = path.read_text(encoding="utf-8")

    assert len(llm.calls) == 1
    assert "行情数据缺失" in content
    assert "+0.00%" not in content


def test_full_dry_run_report_hash_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    project_config = Path(main.__file__).with_name("config.yaml")
    monkeypatch.setattr(main, "load_env", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "build_llm", lambda cfg: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "get_source", lambda market: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "datetime", FixedDateTime)
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))

    path = Path(main.run(str(project_config), do_notify=True, dry_run=True))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    assert digest == "8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0"
