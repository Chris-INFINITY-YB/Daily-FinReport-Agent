from datetime import datetime, timezone

import pytest

from daily_report_agent.models.analysis import AnalysisInput
from daily_report_agent.models.issues import DataIssue, IssueCategory, IssueSeverity
from daily_report_agent.models.market import PriceWindow
from daily_report_agent.models.security import Security


def _issue(severity: IssueSeverity) -> DataIssue:
    return DataIssue(
        severity=severity,
        category=IssueCategory.MISSING_DATA,
        provider="test",
        operation="collect",
        message="测试问题",
        retryable=False,
        occurred_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )


def test_analysis_input_read_only_helpers_distinguish_issue_severity() -> None:
    warning = _issue(IssueSeverity.WARNING)
    blocking = _issue(IssueSeverity.ERROR)
    analysis_input = AnalysisInput(
        security=Security(market="us", symbol="AAPL", name="苹果"),
        price_window=PriceWindow(
            start_price=200.0,
            end_price=200.0,
            period_pct_change=0.0,
        ),
        issues=(warning, blocking),
    )

    assert analysis_input.has_news is False
    assert analysis_input.has_market_data is True
    assert analysis_input.warning_issues == (warning,)
    assert analysis_input.blocking_issues == (blocking,)


def test_analysis_input_rejects_mutable_collection_fields() -> None:
    with pytest.raises(TypeError, match="news 必须是 tuple"):
        AnalysisInput(  # type: ignore[arg-type]
            security=Security(market="us", symbol="AAPL", name="苹果"),
            news=[],
        )
