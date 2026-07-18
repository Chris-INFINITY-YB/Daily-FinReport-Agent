from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from daily_report_agent.models.issues import IssueCategory, IssueSeverity
from daily_report_agent.models.profile import SecurityProfile
from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import (
    ProviderParseError,
    ProviderValidationError,
)
from daily_report_agent.providers.eastmoney.parser import (
    parse_eastmoney_profile_rows,
)


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
SECURITY = Security("cn", "600519", "贵州茅台")


def row(item: str, value: object):
    return MappingProxyType({"item": item, "value": value})


def test_parses_exact_candidate_fields_without_changing_identity() -> None:
    result = parse_eastmoney_profile_rows(
        (
            row("股票简称", " 贵州茅台 "),
            row("行业", " 白酒 "),
            row("股票代码", "000001"),
            row("交易所", "猜测值"),
            row("币种", "CNY"),
            row("总市值", "dynamic-value"),
            row("流通市值", "dynamic-value"),
        ),
        security=SECURITY,
        fetched_at=NOW,
    )

    assert result.provider.provider_id == "eastmoney"
    assert result.issues == ()
    assert len(result.items) == 1
    profile = result.items[0]
    assert isinstance(profile, SecurityProfile)
    assert profile.symbol == "600519"
    assert profile.market == "cn"
    assert profile.name == "贵州茅台"
    assert profile.industry == "白酒"
    assert profile.exchange is None
    assert profile.currency is None
    assert profile.description is None
    assert profile.source == "eastmoney"
    assert profile.fetched_at is NOW


def test_partial_profile_keeps_item_and_adds_missing_data_issue() -> None:
    result = parse_eastmoney_profile_rows(
        (row("股票简称", "贵州茅台"),),
        security=SECURITY,
        fetched_at=NOW,
    )

    assert result.items[0].industry is None
    assert [(issue.severity, issue.category, issue.code) for issue in result.issues] == [
        (
            IssueSeverity.WARNING,
            IssueCategory.MISSING_DATA,
            "missing_profile_fields",
        )
    ]
    assert result.issues[0].message.endswith("industry")


@pytest.mark.parametrize(
    "rows",
    [
        (),
        (row("总市值", "dynamic-value"),),
        (row("股票简称", None), row("行业", "未知")),
    ],
)
def test_recognizable_rows_without_profile_data_are_empty_success(rows) -> None:
    result = parse_eastmoney_profile_rows(
        rows,
        security=SECURITY,
        fetched_at=NOW,
    )

    assert result.items == ()
    assert len(result.issues) == 1
    assert result.issues[0].severity is IssueSeverity.INFO
    assert result.issues[0].code == "profile_not_found"


@pytest.mark.parametrize(
    "rows",
    [
        [row("股票简称", "贵州茅台")],
        (MappingProxyType({"unexpected": "value"}),),
        (MappingProxyType({"item": "股票简称"}),),
        (MappingProxyType({"item": 1, "value": "贵州茅台"}),),
    ],
)
def test_nonempty_unrecognized_structure_raises_parse_error(rows) -> None:
    with pytest.raises(ProviderParseError) as caught:
        parse_eastmoney_profile_rows(
            rows,  # type: ignore[arg-type]
            security=SECURITY,
            fetched_at=NOW,
        )

    assert caught.value.code == "invalid_profile_response"
    assert caught.value.__cause__ is not None


def test_parser_requires_cn_security_and_aware_time() -> None:
    with pytest.raises(ProviderValidationError) as market_error:
        parse_eastmoney_profile_rows(
            (),
            security=Security("us", "AAPL", "Apple"),
            fetched_at=NOW,
        )
    assert market_error.value.code == "unsupported_market"

    with pytest.raises(ProviderValidationError, match="timezone-aware"):
        parse_eastmoney_profile_rows(
            (),
            security=SECURITY,
            fetched_at=datetime(2026, 7, 18),
        )
