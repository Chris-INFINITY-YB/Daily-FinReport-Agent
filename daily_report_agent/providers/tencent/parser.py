"""腾讯行情文本到标准 MarketSnapshot 的纯解析器。"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import is_timezone_aware
from daily_report_agent.models.security import Security
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.errors import (
    ProviderParseError,
    ProviderValidationError,
)

from .constants import TENCENT_QUOTE_DESCRIPTOR
from .symbols import build_tencent_symbol_list


_ASSIGNMENT_PATTERN = re.compile(r'^v_((?:sh|sz)\d{6})="([^"]*)"$')
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_MISSING_VALUES = frozenset({"", "--"})


class _RecordParseError(ValueError):
    pass


def _issue(
    *,
    category: IssueCategory,
    message: str,
    occurred_at: datetime,
    code: str,
) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.WARNING,
        category=category,
        provider=TENCENT_QUOTE_DESCRIPTOR.provider_id,
        operation="parse_quotes",
        message=message,
        retryable=False,
        occurred_at=occurred_at,
        code=code,
    )


def _parse_number(value: str, field_name: str) -> float | None:
    normalized = value.strip()
    if normalized in _MISSING_VALUES:
        return None
    if "%" in normalized:
        raise _RecordParseError(f"{field_name} contains an unsupported percent sign")
    try:
        return float(normalized)
    except ValueError as exc:
        raise _RecordParseError(f"{field_name} is not numeric") from exc


def _parse_observed_at(
    value: str,
    *,
    fetched_at: datetime,
) -> tuple[datetime, DataIssue | None]:
    normalized = value.strip()
    if normalized:
        try:
            parsed = datetime.strptime(normalized, "%Y%m%d%H%M%S")
        except ValueError:
            pass
        else:
            return parsed.replace(tzinfo=_SHANGHAI_TZ), None
    return fetched_at, _issue(
        category=IssueCategory.MISSING_DATA,
        message="Quote time was unavailable; fetched_at was used",
        occurred_at=fetched_at,
        code="quote_time_fallback",
    )


def _parse_record(
    payload: str,
    *,
    response_symbol: str,
    fetched_at: datetime,
) -> tuple[MarketSnapshot, DataIssue | None]:
    fields = payload.split("~")
    if len(fields) <= 32:
        raise _RecordParseError("quote record has too few fields")
    if fields[0].strip() != "1":
        raise _RecordParseError("quote record status is unsupported")
    symbol = fields[2].strip()
    if not re.fullmatch(r"\d{6}", symbol):
        raise _RecordParseError("quote record symbol is invalid")
    if response_symbol[2:] != symbol:
        raise _RecordParseError("response prefix and payload symbol do not match")

    observed_at, time_issue = _parse_observed_at(fields[30], fetched_at=fetched_at)
    return MarketSnapshot(
        symbol=symbol,
        observed_at=observed_at,
        source=TENCENT_QUOTE_DESCRIPTOR.provider_id,
        price=_parse_number(fields[3], "price"),
        previous_close=_parse_number(fields[4], "previous_close"),
        pct_change=_parse_number(fields[32], "pct_change"),
        volume=None,
        amount=None,
        turnover=None,
        pe_ttm=None,
        pb=None,
        market_cap=None,
        currency="CNY",
    ), time_issue


def _raise_parse_error(message: str, code: str, cause: Exception) -> None:
    raise ProviderParseError(
        provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
        operation="parse_quotes",
        safe_message=message,
        code=code,
    ) from cause


def parse_tencent_quote_response(
    text: str,
    *,
    requested: tuple[Security, ...],
    fetched_at: datetime,
) -> ProviderResult[MarketSnapshot]:
    """解析固定腾讯文本格式，按请求顺序返回合法快照。"""
    if not isinstance(text, str):
        raise ProviderValidationError(
            provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
            operation="parse_quotes",
            safe_message="Tencent quote response must be text",
            code="invalid_response_type",
        )
    if not is_timezone_aware(fetched_at):
        raise ProviderValidationError(
            provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
            operation="parse_quotes",
            safe_message="fetched_at must be timezone-aware",
            code="invalid_fetched_at",
        )

    requested_symbols = build_tencent_symbol_list(requested)
    if not requested_symbols:
        return ProviderResult(provider=TENCENT_QUOTE_DESCRIPTOR)
    if not text.strip():
        _raise_parse_error(
            "Tencent quote response was empty",
            "empty_response",
            _RecordParseError("empty response"),
        )

    requested_by_symbol = dict(zip(requested_symbols, requested))
    items_by_symbol: dict[str, MarketSnapshot] = {}
    explicit_empty: set[str] = set()
    issues: list[DataIssue] = []
    parse_failures: list[_RecordParseError] = []
    segments = [
        segment.strip()
        for segment in re.split(r";\s*", text.strip())
        if segment.strip()
    ]

    for segment in segments:
        match = _ASSIGNMENT_PATTERN.fullmatch(segment)
        if match is None:
            error = _RecordParseError("quote assignment is malformed")
            parse_failures.append(error)
            issues.append(
                _issue(
                    category=IssueCategory.PARSE,
                    message="A Tencent quote record was malformed",
                    occurred_at=fetched_at,
                    code="malformed_record",
                )
            )
            continue
        response_symbol, payload = match.groups()
        if response_symbol not in requested_by_symbol:
            issues.append(
                _issue(
                    category=IssueCategory.VALIDATION,
                    message="An unrequested Tencent quote record was ignored",
                    occurred_at=fetched_at,
                    code="unrequested_symbol",
                )
            )
            continue
        if response_symbol in items_by_symbol or response_symbol in explicit_empty:
            issues.append(
                _issue(
                    category=IssueCategory.PARSE,
                    message="A duplicate Tencent quote record was ignored",
                    occurred_at=fetched_at,
                    code="duplicate_record",
                )
            )
            continue
        if payload == "":
            explicit_empty.add(response_symbol)
            continue
        try:
            snapshot, time_issue = _parse_record(
                payload,
                response_symbol=response_symbol,
                fetched_at=fetched_at,
            )
        except _RecordParseError as exc:
            parse_failures.append(exc)
            issues.append(
                _issue(
                    category=IssueCategory.PARSE,
                    message="A Tencent quote record could not be parsed",
                    occurred_at=fetched_at,
                    code="malformed_record",
                )
            )
            continue
        items_by_symbol[response_symbol] = snapshot
        if time_issue is not None:
            issues.append(time_issue)

    if not items_by_symbol:
        if explicit_empty == set(requested_symbols) and not parse_failures:
            return ProviderResult(
                provider=TENCENT_QUOTE_DESCRIPTOR,
                issues=tuple(issues),
            )
        cause = parse_failures[0] if parse_failures else _RecordParseError(
            "no requested quote records were parsed"
        )
        _raise_parse_error(
            "Tencent quote response contained no valid requested records",
            "no_valid_records",
            cause,
        )

    for response_symbol in requested_symbols:
        if response_symbol not in items_by_symbol and response_symbol not in explicit_empty:
            issues.append(
                _issue(
                    category=IssueCategory.MISSING_DATA,
                    message="A requested Tencent quote record was missing",
                    occurred_at=fetched_at,
                    code="missing_requested_symbol",
                )
            )

    return ProviderResult(
        provider=TENCENT_QUOTE_DESCRIPTOR,
        items=tuple(
            items_by_symbol[symbol]
            for symbol in requested_symbols
            if symbol in items_by_symbol
        ),
        issues=tuple(issues),
    )
