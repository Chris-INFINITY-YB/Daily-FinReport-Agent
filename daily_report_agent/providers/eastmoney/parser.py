"""Eastmoney Profile 只读行记录到标准 SecurityProfile 的纯解析器。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.models.news import is_timezone_aware
from daily_report_agent.models.profile import SecurityProfile
from daily_report_agent.models.security import Security
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.errors import (
    ProviderParseError,
    ProviderValidationError,
)

from .constants import EASTMONEY_PROFILE_DESCRIPTOR
from .transport import ProfileRows


_SUPPORTED_ITEMS = {
    "股票简称": "name",
    "行业": "industry",
}
_DISALLOWED_PLACEHOLDERS = frozenset({"未知", "None"})


class _RowParseError(ValueError):
    pass


def _validation_error(message: str, code: str) -> ProviderValidationError:
    return ProviderValidationError(
        provider_id=EASTMONEY_PROFILE_DESCRIPTOR.provider_id,
        operation="fetch_profile",
        safe_message=message,
        code=code,
    )


def _parse_error(message: str, code: str, cause: Exception) -> None:
    raise ProviderParseError(
        provider_id=EASTMONEY_PROFILE_DESCRIPTOR.provider_id,
        operation="fetch_profile",
        safe_message=message,
        code=code,
    ) from cause


def _missing_issue(
    *,
    fetched_at: datetime,
    severity: IssueSeverity,
    message: str,
    code: str,
) -> DataIssue:
    return DataIssue(
        severity=severity,
        category=IssueCategory.MISSING_DATA,
        provider=EASTMONEY_PROFILE_DESCRIPTOR.provider_id,
        operation="fetch_profile",
        message=message,
        retryable=False,
        occurred_at=fetched_at,
        code=code,
    )


def _candidate_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized in _DISALLOWED_PLACEHOLDERS:
        return None
    return normalized


def parse_eastmoney_profile_rows(
    rows: ProfileRows,
    *,
    security: Security,
    fetched_at: datetime,
) -> ProviderResult[SecurityProfile]:
    """解析契约允许的精确候选字段；不推断身份或未支持字段。"""
    if not isinstance(security, Security):
        raise _validation_error("Security input is invalid", "invalid_security")
    if security.market != "cn":
        raise _validation_error(
            "Eastmoney profile provider only supports the cn market",
            "unsupported_market",
        )
    if not is_timezone_aware(fetched_at):
        raise _validation_error(
            "fetched_at must be a timezone-aware datetime",
            "invalid_fetched_at",
        )
    if not isinstance(rows, tuple):
        _parse_error(
            "Eastmoney profile rows must be an immutable tuple",
            "invalid_profile_response",
            _RowParseError("rows are not a tuple"),
        )
    if not rows:
        return ProviderResult(
            provider=EASTMONEY_PROFILE_DESCRIPTOR,
            issues=(
                _missing_issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.INFO,
                    message="Eastmoney profile data was unavailable",
                    code="profile_not_found",
                ),
            ),
        )

    parsed: dict[str, str | None] = {}
    for row in rows:
        if not isinstance(row, Mapping) or "item" not in row or "value" not in row:
            _parse_error(
                "Eastmoney profile response structure was not recognized",
                "invalid_profile_response",
                _RowParseError("row must contain item and value"),
            )
        item = row["item"]
        if not isinstance(item, str) or not item.strip():
            _parse_error(
                "Eastmoney profile response structure was not recognized",
                "invalid_profile_response",
                _RowParseError("item must be non-empty text"),
            )
        field_name = _SUPPORTED_ITEMS.get(item.strip())
        if field_name is None:
            continue
        if field_name in parsed:
            _parse_error(
                "Eastmoney profile response contained a duplicate field",
                "invalid_profile_response",
                _RowParseError("duplicate supported item"),
            )
        parsed[field_name] = _candidate_value(row["value"])

    name = parsed.get("name")
    industry = parsed.get("industry")
    if name is None and industry is None:
        return ProviderResult(
            provider=EASTMONEY_PROFILE_DESCRIPTOR,
            issues=(
                _missing_issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.INFO,
                    message="Eastmoney profile data was unavailable",
                    code="profile_not_found",
                ),
            ),
        )

    missing = tuple(
        field_name
        for field_name, value in (("name", name), ("industry", industry))
        if value is None
    )
    issues = ()
    if missing:
        issues = (
            _missing_issue(
                fetched_at=fetched_at,
                severity=IssueSeverity.WARNING,
                message=(
                    "Eastmoney profile fields were unavailable: "
                    + ", ".join(missing)
                ),
                code="missing_profile_fields",
            ),
        )

    profile = SecurityProfile(
        symbol=security.symbol,
        market=security.market,
        name=name,
        exchange=None,
        currency=None,
        industry=industry,
        description=None,
        source=EASTMONEY_PROFILE_DESCRIPTOR.provider_id,
        fetched_at=fetched_at,
    )
    return ProviderResult(
        provider=EASTMONEY_PROFILE_DESCRIPTOR,
        items=(profile,),
        issues=issues,
    )
