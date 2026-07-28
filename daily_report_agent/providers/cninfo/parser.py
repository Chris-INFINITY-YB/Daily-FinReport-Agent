"""CNInfo 宽表行记录到标准 SecurityProfile 的纯解析器。"""

from __future__ import annotations

import re
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

from .constants import CNINFO_PROFILE_DESCRIPTOR
from .transport import CninfoProfileRows


_CN_SYMBOL_PATTERN = re.compile(r"^\d{6}$", re.ASCII)
_SYNTHETIC_EMPTY_PLACEHOLDERS = frozenset(
    {"SYNTHETIC_EMPTY", "SYNTHETIC_MISSING", "SYNTHETIC_NULL"}
)


class _RowParseError(ValueError):
    pass


def _validation_error(message: str, code: str) -> ProviderValidationError:
    return ProviderValidationError(
        provider_id=CNINFO_PROFILE_DESCRIPTOR.provider_id,
        operation="fetch_profile",
        safe_message=message,
        code=code,
    )


def _parse_error(message: str, code: str, cause: Exception) -> None:
    raise ProviderParseError(
        provider_id=CNINFO_PROFILE_DESCRIPTOR.provider_id,
        operation="fetch_profile",
        safe_message=message,
        code=code,
    ) from cause


def _issue(
    *,
    fetched_at: datetime,
    severity: IssueSeverity,
    category: IssueCategory,
    message: str,
    code: str,
) -> DataIssue:
    return DataIssue(
        severity=severity,
        category=category,
        provider=CNINFO_PROFILE_DESCRIPTOR.provider_id,
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
    if not normalized or normalized in _SYNTHETIC_EMPTY_PLACEHOLDERS:
        return None
    return normalized


def _validate_identity(row: Mapping[str, object], security: Security) -> bool:
    if "A股代码" not in row:
        return False
    value = row["A股代码"]
    if not isinstance(value, str):
        _parse_error(
            "CNInfo profile identity field was invalid",
            "invalid_profile_identity",
            _RowParseError("identity must be text"),
        )
    normalized = value.strip()
    if _CN_SYMBOL_PATTERN.fullmatch(normalized) is None:
        _parse_error(
            "CNInfo profile identity field was invalid",
            "invalid_profile_identity",
            _RowParseError("identity must contain six ASCII digits"),
        )
    if normalized != security.symbol:
        raise _validation_error(
            "CNInfo profile identity did not match the requested security",
            "profile_identity_mismatch",
        )
    return True


def parse_cninfo_profile_rows(
    rows: CninfoProfileRows,
    *,
    security: Security,
    fetched_at: datetime,
) -> ProviderResult[SecurityProfile]:
    """解析零行或一行宽表；不推断身份、市场或未支持字段。"""
    if not isinstance(security, Security):
        raise _validation_error("Security input is invalid", "invalid_security")
    if security.market != "cn":
        raise _validation_error(
            "CNInfo profile provider only supports the cn market",
            "unsupported_market",
        )
    if _CN_SYMBOL_PATTERN.fullmatch(security.symbol) is None:
        raise _validation_error(
            "CN security symbol must contain exactly six ASCII digits",
            "invalid_symbol",
        )
    if not is_timezone_aware(fetched_at):
        raise _validation_error(
            "fetched_at must be a timezone-aware datetime",
            "invalid_fetched_at",
        )
    if not isinstance(rows, tuple):
        _parse_error(
            "CNInfo profile rows must be an immutable tuple",
            "invalid_profile_response",
            _RowParseError("rows are not a tuple"),
        )
    if not rows:
        return ProviderResult(
            provider=CNINFO_PROFILE_DESCRIPTOR,
            issues=(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.INFO,
                    category=IssueCategory.MISSING_DATA,
                    message="CNInfo profile data was unavailable",
                    code="profile_not_found",
                ),
            ),
        )
    if len(rows) != 1:
        _parse_error(
            "CNInfo profile response contained an unexpected row count",
            "invalid_profile_response",
            _RowParseError("profile response must contain at most one row"),
        )

    row = rows[0]
    if not isinstance(row, Mapping):
        _parse_error(
            "CNInfo profile response structure was not recognized",
            "invalid_profile_response",
            _RowParseError("profile row must be a mapping"),
        )
    if any(not isinstance(column, str) or not column.strip() for column in row):
        _parse_error(
            "CNInfo profile response contained an invalid column",
            "invalid_profile_response",
            _RowParseError("column names must be non-empty text"),
        )

    identity_verified = _validate_identity(row, security)
    name = _candidate_value(row.get("A股简称"))
    industry = _candidate_value(row.get("所属行业"))

    issues: list[DataIssue] = []
    if not identity_verified:
        issues.append(
            _issue(
                fetched_at=fetched_at,
                severity=IssueSeverity.WARNING,
                category=IssueCategory.VALIDATION,
                message="CNInfo profile response identity was not available for verification",
                code="profile_identity_unverified",
            )
        )

    if name is None and industry is None:
        issues.append(
            _issue(
                fetched_at=fetched_at,
                severity=IssueSeverity.INFO,
                category=IssueCategory.MISSING_DATA,
                message="CNInfo profile data was unavailable",
                code="profile_not_found",
            )
        )
        return ProviderResult(
            provider=CNINFO_PROFILE_DESCRIPTOR,
            issues=tuple(issues),
        )

    missing = tuple(
        field_name
        for field_name, value in (("name", name), ("industry", industry))
        if value is None
    )
    if missing:
        issues.append(
            _issue(
                fetched_at=fetched_at,
                severity=IssueSeverity.WARNING,
                category=IssueCategory.MISSING_DATA,
                message="CNInfo profile fields were unavailable: " + ", ".join(missing),
                code="missing_profile_fields",
            )
        )

    profile = SecurityProfile(
        symbol=security.symbol,
        market=security.market,
        name=name,
        exchange=None,
        currency=None,
        industry=industry,
        description=None,
        source=CNINFO_PROFILE_DESCRIPTOR.provider_id,
        fetched_at=fetched_at,
    )
    return ProviderResult(
        provider=CNINFO_PROFILE_DESCRIPTOR,
        items=(profile,),
        issues=tuple(issues),
    )
