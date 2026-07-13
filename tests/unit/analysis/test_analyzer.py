from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from daily_report_agent.analyzer import (
    SYSTEM_PROMPT,
    analyze,
    analyze_input,
    analyze_stockdata,
    build_user_prompt,
)
from daily_report_agent.datasource.base import NewsItem as LegacyNewsItem
from daily_report_agent.datasource.base import StockData
from daily_report_agent.models.analysis import AnalysisInput
from daily_report_agent.models.issues import DataIssue, IssueCategory, IssueSeverity
from daily_report_agent.models.market import PriceWindow
from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.security import Security
from daily_report_agent.report import render


class FakeLLM:
    def __init__(self, result: str = "固定分析结果") -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.result


def _full_stockdata() -> StockData:
    return StockData(
        symbol="AAPL",
        name="苹果",
        market="us",
        intro="苹果公司简介",
        start_date="2026-07-06",
        end_date="2026-07-13",
        start_price=200.0,
        end_price=204.0,
        pct_change=2.0,
        news=[
            LegacyNewsItem(
                date="2026-07-12",
                headline="发布新品",
                summary="新品摘要",
            )
        ],
    )


def _standard_news() -> NewsItem:
    when = datetime(2026, 7, 12, tzinfo=timezone.utc)
    return NewsItem(
        id="news-1",
        external_id=None,
        source="fixture",
        source_type="test",
        title="固定新闻",
        summary="固定摘要",
        content=None,
        url=None,
        published_at=when,
        fetched_at=when,
        language="zh",
        content_hash="hash-1",
    )


def _issue(severity: IssueSeverity) -> DataIssue:
    return DataIssue(
        severity=severity,
        category=IssueCategory.NETWORK,
        provider="fixture",
        operation="fetch",
        message="不应进入 Prompt 的 /private/path?token=secret",
        retryable=False,
        occurred_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )


def test_complete_legacy_prompt_is_exactly_unchanged_and_llm_called_once() -> None:
    data = _full_stockdata()
    llm = FakeLLM()
    prompt_before_migration = build_user_prompt(data)

    result = analyze(data, llm)

    assert result == "固定分析结果"
    assert len(llm.calls) == 1
    assert llm.calls[0] == (SYSTEM_PROMPT, prompt_before_migration)
    assert hashlib.sha256(prompt_before_migration.encode()).hexdigest() == (
        "7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3"
    )


def test_market_failure_with_news_and_legacy_error_still_calls_llm() -> None:
    data = StockData(
        symbol="AAPL",
        name="苹果",
        market="us",
        start_price=0.0,
        end_price=0.0,
        pct_change=0.0,
        news=[LegacyNewsItem(date="2026-07-12", headline="仍有新闻")],
        error="行情抓取失败",
    )
    llm = FakeLLM()

    analysis_input, result = analyze_stockdata(data, llm)
    prompt = llm.calls[0][1]

    assert result == "固定分析结果"
    assert len(llm.calls) == 1
    assert analysis_input.price_window is None
    assert analysis_input.warning_issues
    assert "行情数据缺失" in prompt
    assert "起始价格：0" not in prompt
    assert "结束价格：0" not in prompt
    assert "涨跌幅：0" not in prompt
    report = render(
        [{"symbol": "AAPL", "name": "苹果", "market": "us", "pct_change": None, "analysis": result}],
        "2026-07-13",
    )
    assert "+0.00%" not in report
    assert "行情数据缺失" in report


def test_news_failure_with_market_still_calls_llm_and_keeps_price_window() -> None:
    data = StockData(
        symbol="AAPL",
        name="苹果",
        market="us",
        start_price=200.0,
        end_price=204.0,
        pct_change=2.0,
        news=[],
        error="新闻抓取失败",
    )
    llm = FakeLLM()

    analysis_input, result = analyze_stockdata(data, llm)

    assert result == "固定分析结果"
    assert len(llm.calls) == 1
    assert analysis_input.price_window == PriceWindow(200.0, 204.0, 2.0)
    assert "没有可用的新增新闻" in llm.calls[0][1]


def test_missing_profile_does_not_block_or_fabricate_profile() -> None:
    data = StockData(
        symbol="AAPL",
        name="苹果",
        market="us",
        news=[LegacyNewsItem(date="2026-07-12", headline="仍有新闻")],
    )
    llm = FakeLLM()

    result = analyze(data, llm)

    assert result == "固定分析结果"
    assert len(llm.calls) == 1
    assert "公司简介数据缺失" in llm.calls[0][1]


def test_no_news_or_market_skips_llm_without_directional_judgment() -> None:
    llm = FakeLLM()

    result = analyze(StockData(symbol="AAPL", name="苹果", market="us"), llm)

    assert llm.calls == []
    assert "数据不足" in result
    assert all(direction not in result for direction in ("看涨", "看跌", "中性"))


def test_real_zero_pct_change_is_preserved_and_reported() -> None:
    data = StockData(
        symbol="AAPL",
        name="苹果",
        market="us",
        start_price=200.0,
        end_price=200.0,
        pct_change=0.0,
    )
    llm = FakeLLM()

    analysis_input, result = analyze_stockdata(data, llm)

    assert len(llm.calls) == 1
    assert analysis_input.price_window is not None
    assert analysis_input.price_window.period_pct_change == 0.0
    report = render(
        [{"symbol": "AAPL", "name": "苹果", "market": "us", "pct_change": 0.0, "analysis": result}],
        "2026-07-13",
    )
    assert "📈 +0.00%" in report


@pytest.mark.parametrize("severity", [IssueSeverity.WARNING, IssueSeverity.INFO])
def test_non_blocking_issues_do_not_skip_llm(severity: IssueSeverity) -> None:
    llm = FakeLLM()
    analysis_input = AnalysisInput(
        security=Security(market="us", symbol="AAPL", name="苹果"),
        news=(_standard_news(),),
        issues=(_issue(severity),),
    )

    result = analyze_input(analysis_input, llm)

    assert result == "固定分析结果"
    assert len(llm.calls) == 1
    assert "secret" not in llm.calls[0][1]
    assert "/private/path" not in llm.calls[0][1]


def test_blocking_error_skips_llm() -> None:
    llm = FakeLLM()
    analysis_input = AnalysisInput(
        security=Security(market="us", symbol="AAPL", name="苹果"),
        news=(_standard_news(),),
        issues=(_issue(IssueSeverity.ERROR),),
    )

    result = analyze_input(analysis_input, llm)

    assert llm.calls == []
    assert "阻断性数据错误" in result
    assert all(direction not in result for direction in ("看涨", "看跌", "中性"))
