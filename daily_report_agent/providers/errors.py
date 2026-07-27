"""Provider 的安全错误类型及 DataIssue 映射。"""

from __future__ import annotations

import re
from datetime import datetime

from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.models.news import is_timezone_aware

from .contracts import (
    normalize_provider_error_code,
    normalize_provider_id,
    normalize_provider_operation,
)


_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[\s_-]?key|token|authorization)\b\s*[:=]\s*[^\s,;]+"
)


def _sanitize_safe_message(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("safe_message 不能为空")
    sanitized = _URL_PATTERN.sub("[redacted-url]", value.strip())
    sanitized = _BEARER_PATTERN.sub("Bearer [redacted]", sanitized)
    sanitized = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)} [redacted]", sanitized)
    return sanitized


class ProviderError(Exception):
    """仅保存可安全外显的错误字段；底层原因通过异常链保留。"""

    default_retryable = False

    def __init__(
        self,
        *,
        provider_id: str,
        operation: str,
        safe_message: str,
        code: str | None = None,
        retryable: bool | None = None,
        http_status: int | None = None,
    ) -> None:
        self.provider_id = normalize_provider_id(provider_id)
        self.operation = normalize_provider_operation(operation)
        self.safe_message = _sanitize_safe_message(safe_message)
        self.code = normalize_provider_error_code(code)
        if retryable is not None and not isinstance(retryable, bool):
            raise TypeError("retryable 必须是 bool 或 None")
        self.retryable = self.default_retryable if retryable is None else retryable
        if http_status is not None and (
            not isinstance(http_status, int) or isinstance(http_status, bool)
        ):
            raise TypeError("http_status 必须是 int 或 None")
        self.http_status = http_status
        super().__init__(self.safe_message)


class ProviderNetworkError(ProviderError):
    default_retryable = True


class ProviderTimeoutError(ProviderNetworkError):
    pass


class ProviderRateLimitError(ProviderError):
    default_retryable = True


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderParseError(ProviderError):
    pass


class ProviderValidationError(ProviderError):
    pass


class ProviderBlockedError(ProviderError):
    default_retryable = True


class ProviderUnavailableError(ProviderError):
    default_retryable = True


_CATEGORY_BY_ERROR_TYPE: tuple[tuple[type[ProviderError], IssueCategory], ...] = (
    (ProviderTimeoutError, IssueCategory.NETWORK),
    (ProviderNetworkError, IssueCategory.NETWORK),
    (ProviderRateLimitError, IssueCategory.RATE_LIMIT),
    (ProviderAuthenticationError, IssueCategory.AUTH),
    (ProviderParseError, IssueCategory.PARSE),
    (ProviderValidationError, IssueCategory.VALIDATION),
    (ProviderBlockedError, IssueCategory.PROVIDER_UNAVAILABLE),
    (ProviderUnavailableError, IssueCategory.PROVIDER_UNAVAILABLE),
)


def provider_error_to_issue(
    error: ProviderError,
    *,
    occurred_at: datetime,
) -> DataIssue:
    """将标准异常纯映射为 DataIssue，不执行日志、I/O 或持久化。"""
    if not isinstance(error, ProviderError):
        raise TypeError("error 必须是 ProviderError")
    if not is_timezone_aware(occurred_at):
        raise ValueError("occurred_at 必须是 timezone-aware datetime")
    category = next(
        (
            issue_category
            for error_type, issue_category in _CATEGORY_BY_ERROR_TYPE
            if isinstance(error, error_type)
        ),
        IssueCategory.UNKNOWN,
    )
    return DataIssue(
        severity=IssueSeverity.ERROR,
        category=category,
        provider=error.provider_id,
        operation=error.operation,
        message=error.safe_message,
        retryable=error.retryable,
        occurred_at=occurred_at,
        code=error.code,
        http_status=error.http_status,
        details=None,
    )
