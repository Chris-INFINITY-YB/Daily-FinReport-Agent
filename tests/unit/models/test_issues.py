from datetime import datetime, timezone

from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)


def test_data_issue_enums_and_creation() -> None:
    issue = DataIssue(
        severity=IssueSeverity.WARNING,
        category=IssueCategory.MISSING_DATA,
        provider="fixture",
        operation="quote",
        message="行情缺失",
        retryable=True,
        occurred_at=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
    )

    assert IssueSeverity.ERROR.value == "error"
    assert IssueSeverity.INFO.value == "info"
    assert IssueCategory.PROVIDER_UNAVAILABLE.value == "provider_unavailable"
    assert issue.severity is IssueSeverity.WARNING
    assert issue.category is IssueCategory.MISSING_DATA
