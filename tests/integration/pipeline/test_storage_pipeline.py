from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_report_agent import main
from daily_report_agent.datasource.base import NewsItem as LegacyNewsItem
from daily_report_agent.datasource.base import StockData
from daily_report_agent.models.analysis import AnalysisInput
from daily_report_agent.models.market import MarketSnapshot, PriceWindow
from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.security import Security


WHEN = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return "固定分析结果"


class Source:
    def __init__(self, data: StockData) -> None:
        self.data = data

    def fetch(self, symbol, name, news_days, max_news) -> StockData:
        return self.data


def _legacy_data() -> StockData:
    return StockData(
        symbol="TEST",
        name="测试标的",
        market="us",
        intro="固定简介",
        start_date="2026-07-06",
        end_date="2026-07-13",
        start_price=100.0,
        end_price=101.0,
        pct_change=1.0,
        news=[LegacyNewsItem(date="2026-07-12", headline="固定新闻")],
    )


def _analysis_input() -> AnalysisInput:
    news = NewsItem(
        id="news-1",
        external_id="external-1",
        source="fixture",
        source_type="wire",
        title="固定新闻",
        summary="固定摘要",
        content=None,
        url=None,
        published_at=WHEN,
        fetched_at=WHEN,
        language="zh",
        content_hash="hash-1",
        related_symbols=("TEST",),
    )
    snapshot = MarketSnapshot(
        symbol="TEST",
        observed_at=WHEN,
        source="fixture",
        price=101.0,
        previous_close=100.0,
        pct_change=1.0,
    )
    return AnalysisInput(
        security=Security(market="us", symbol="TEST", name="测试标的"),
        profile_text="固定简介",
        news=(news,),
        price_window=PriceWindow(100.0, 101.0, 1.0),
        market_snapshots=(snapshot,),
    )


def _write_config(
    path: Path,
    *,
    storage_enabled: bool | None,
    database_path: Path,
) -> None:
    storage = ""
    if storage_enabled is not None:
        enabled = "true" if storage_enabled else "false"
        storage = f"\nstorage:\n  enabled: {enabled}\n  path: {database_path}\n"
    path.write_text(
        (
            "watchlist:\n"
            "  - {market: us, symbol: TEST, name: 测试标的}\n"
            "data:\n"
            "  news_days: 7\n"
            "  max_news_per_stock: 3\n"
            "llm:\n"
            "  provider: fixture\n"
            f"{storage}"
            "notify:\n"
            "  email: false\n"
        ),
        encoding="utf-8",
    )


def _patch_online_path(monkeypatch, tmp_path: Path, data: StockData, llm: FakeLLM):
    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "build_llm", lambda cfg: llm)
    monkeypatch.setattr(main, "get_source", lambda market: Source(data))
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))


@pytest.mark.parametrize("storage_enabled", [False, None])
def test_disabled_or_legacy_config_creates_no_database(
    tmp_path: Path,
    monkeypatch,
    storage_enabled: bool | None,
) -> None:
    database_path = tmp_path / "disabled" / "agent.sqlite"
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        storage_enabled=storage_enabled,
        database_path=database_path,
    )
    llm = FakeLLM()
    _patch_online_path(monkeypatch, tmp_path, _legacy_data(), llm)

    report_path = Path(main.run(str(config_path), do_notify=False, dry_run=False))

    assert report_path.exists()
    assert len(llm.calls) == 1
    assert not database_path.exists()


def test_disabled_context_does_not_import_storage_in_clean_process(tmp_path: Path) -> None:
    database_path = tmp_path / "never-created.sqlite"
    code = (
        "import sys; "
        "from daily_report_agent.pipeline.runner import start_run_context; "
        f"c=start_run_context({{'storage':{{'enabled':False,'path':r'{database_path}'}}}},"
        "config_path='config.yaml',dry_run=False); "
        "assert not c.storage_enabled; "
        "assert 'daily_report_agent.storage' not in sys.modules"
    )
    root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not database_path.exists()


def test_enabled_storage_persists_models_and_finishes_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "storage" / "agent.sqlite"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, storage_enabled=True, database_path=database_path)
    llm = FakeLLM()
    _patch_online_path(monkeypatch, tmp_path, _legacy_data(), llm)
    monkeypatch.setattr(main, "stockdata_to_analysis_input", lambda data: _analysis_input())

    report_path = Path(main.run(str(config_path), do_notify=False, dry_run=False))

    assert report_path.exists()
    assert len(llm.calls) == 1
    assert database_path.exists()
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        run = connection.execute("SELECT * FROM pipeline_runs").fetchone()
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "securities",
                "news_items",
                "news_security_links",
                "market_snapshots",
                "provider_calls",
            )
        }
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert run["status"] == "success"
    assert run["dry_run"] == 0
    assert run["created_news"] == 1
    assert run["created_snapshots"] == 1
    assert counts == {
        "securities": 1,
        "news_items": 1,
        "news_security_links": 1,
        "market_snapshots": 1,
        "provider_calls": 0,
    }
    assert "price_windows" not in table_names


def test_sqlite_write_failure_degrades_to_partial_but_report_continues(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from daily_report_agent.storage.repositories import SecurityRepository

    database_path = tmp_path / "storage" / "agent.sqlite"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, storage_enabled=True, database_path=database_path)
    llm = FakeLLM()
    _patch_online_path(monkeypatch, tmp_path, _legacy_data(), llm)

    def fail_write(self, security):
        raise sqlite3.OperationalError("simulated write failure")

    monkeypatch.setattr(SecurityRepository, "upsert_security", fail_write)

    report_path = Path(main.run(str(config_path), do_notify=False, dry_run=False))

    assert report_path.exists()
    assert len(llm.calls) == 1
    assert "存储警告" in capsys.readouterr().out
    with closing(sqlite3.connect(database_path)) as connection:
        status = connection.execute("SELECT status FROM pipeline_runs").fetchone()[0]
        security_count = connection.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    assert status == "partial"
    assert security_count == 0


def test_unrecoverable_pipeline_error_finishes_run_as_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "storage" / "agent.sqlite"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, storage_enabled=True, database_path=database_path)
    llm = FakeLLM()
    _patch_online_path(monkeypatch, tmp_path, _legacy_data(), llm)
    monkeypatch.setattr(main.report, "save", lambda content, date: (_ for _ in ()).throw(RuntimeError("report failure")))

    with pytest.raises(RuntimeError, match="report failure"):
        main.run(str(config_path), do_notify=False, dry_run=False)

    with closing(sqlite3.connect(database_path)) as connection:
        run = connection.execute(
            "SELECT status, error_summary FROM pipeline_runs"
        ).fetchone()
    assert run == ("failed", "RuntimeError: 日报运行失败")


def test_dry_run_never_creates_database_even_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "storage" / "agent.sqlite"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, storage_enabled=True, database_path=database_path)
    monkeypatch.setattr(main, "load_env", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "build_llm", lambda cfg: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "get_source", lambda market: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))

    report_path = Path(main.run(str(config_path), do_notify=True, dry_run=True))

    assert report_path.exists()
    assert not database_path.exists()
