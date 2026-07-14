from __future__ import annotations

from datetime import datetime, timezone

import pytest

from daily_report_agent.models.issues import IssueCategory, IssueSeverity
from daily_report_agent.providers.errors import (
    ProviderAuthenticationError,
    ProviderBlockedError,
    ProviderError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    provider_error_to_issue,
)


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("error_type", "category", "retryable"),
    [
        (ProviderTimeoutError, IssueCategory.NETWORK, True),
        (ProviderNetworkError, IssueCategory.NETWORK, True),
        (ProviderRateLimitError, IssueCategory.RATE_LIMIT, True),
        (ProviderAuthenticationError, IssueCategory.AUTH, False),
        (ProviderParseError, IssueCategory.PARSE, False),
        (ProviderValidationError, IssueCategory.VALIDATION, False),
        (ProviderBlockedError, IssueCategory.PROVIDER_UNAVAILABLE, True),
        (ProviderUnavailableError, IssueCategory.PROVIDER_UNAVAILABLE, True),
        (ProviderError, IssueCategory.UNKNOWN, False),
    ],
)
def test_error_mapping(
    error_type: type[ProviderError],
    category: IssueCategory,
    retryable: bool,
) -> None:
    error = error_type(
        provider_id="test-provider",
        operation="fetch_news",
        safe_message="Safe provider failure",
        code="provider_failure",
        http_status=503,
    )
    issue = provider_error_to_issue(error, occurred_at=NOW)
    assert issue.category is category
    assert issue.retryable is retryable
    assert issue.severity is IssueSeverity.ERROR
    assert issue.provider == "test-provider"
    assert issue.operation == "fetch_news"
    assert issue.message == "Safe provider failure"
    assert issue.code == "provider_failure"
    assert issue.http_status == 503
    assert issue.details is None


def test_unknown_error_preserves_explicit_retryable() -> None:
    error = ProviderError(
        provider_id="test-provider",
        operation="fetch",
        safe_message="Safe failure",
        retryable=True,
    )
    assert provider_error_to_issue(error, occurred_at=NOW).retryable is True


def test_error_mapping_rejects_naive_time() -> None:
    error = ProviderNetworkError(
        provider_id="test-provider",
        operation="fetch",
        safe_message="Safe failure",
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        provider_error_to_issue(error, occurred_at=datetime(2026, 7, 14))


def test_safe_fields_redact_urls_tokens_and_api_keys() -> None:
    error = ProviderAuthenticationError(
        provider_id="test-provider",
        operation="authenticate",
        safe_message=(
            "request https://api.test/path?api_key=secret failed; "
            "Authorization: Bearer secret-token; API Key=another-secret"
        ),
    )
    issue = provider_error_to_issue(error, occurred_at=NOW)
    lowered = issue.message.lower()
    assert "https://" not in lowered
    assert "secret-token" not in lowered
    assert "another-secret" not in lowered
    assert "traceback" not in lowered


def test_underlying_exception_is_only_available_through_cause() -> None:
    underlying = RuntimeError("Bearer private-token at https://secret.test?q=key")
    captured: ProviderUnavailableError | None = None
    try:
        try:
            raise underlying
        except RuntimeError as exc:
            raise ProviderUnavailableError(
                provider_id="test-provider",
                operation="fetch",
                safe_message="Provider is temporarily unavailable",
            ) from exc
    except ProviderUnavailableError as error:
        captured = error
        issue = provider_error_to_issue(error, occurred_at=NOW)

    assert captured is not None
    assert captured.__cause__ is underlying
    assert "private-token" not in captured.safe_message
    assert "private-token" not in issue.message
    assert issue.details is None
