from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from daily_report_agent.models.issues import IssueCategory, IssueSeverity
from daily_report_agent.models.profile import SecurityProfile
from daily_report_agent.models.security import Security
from daily_report_agent.providers.cninfo.parser import parse_cninfo_profile_rows
from daily_report_agent.providers.errors import (
    ProviderParseError,
    ProviderValidationError,
)


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
SECURITY = Security("cn", "000000", "SYNTHETIC_REQUEST_NAME")
FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "providers"
    / "cninfo"
    / "profile_synthetic_minimal.json"
)


def row(**values: object):
    return MappingProxyType(values)


def synthetic_fixture_rows():
    loaded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return tuple(MappingProxyType(item) for item in loaded)


def parse(rows):
    return parse_cninfo_profile_rows(rows, security=SECURITY, fetched_at=NOW)


def test_parses_normal_synthetic_wide_row_without_changing_identity() -> None:
    result = parse(synthetic_fixture_rows())

    assert result.provider.provider_id == "cninfo"
    assert result.issues == ()
    assert len(result.items) == 1
    profile = result.items[0]
    assert isinstance(profile, SecurityProfile)
    assert profile.symbol == "000000"
    assert profile.market == "cn"
    assert profile.name == "SYNTHETIC_CNINFO_NAME"
    assert profile.industry == "SYNTHETIC_CNINFO_INDUSTRY"
    assert profile.exchange is None
    assert profile.currency is None
    assert profile.description is None
    assert profile.source == "cninfo"
    assert profile.fetched_at is NOW


def test_zero_rows_is_empty_success_with_not_found_issue() -> None:
    result = parse(())

    assert result.items == ()
    assert [(issue.severity, issue.category, issue.code) for issue in result.issues] == [
        (IssueSeverity.INFO, IssueCategory.MISSING_DATA, "profile_not_found")
    ]


def test_multiple_rows_are_rejected_without_selecting_one() -> None:
    rows = synthetic_fixture_rows()

    with pytest.raises(ProviderParseError) as caught:
        parse(rows + rows)

    assert caught.value.code == "invalid_profile_response"
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "rows",
    [
        [row(A股代码="000000", A股简称="SYNTHETIC_NAME")],
        ("SYNTHETIC_NON_MAPPING",),
        (row(**{"": "SYNTHETIC_VALUE"}),),
        (MappingProxyType({1: "SYNTHETIC_VALUE"}),),
    ],
)
def test_invalid_top_level_row_or_column_structure_is_rejected(rows) -> None:
    with pytest.raises(ProviderParseError) as caught:
        parse(rows)  # type: ignore[arg-type]

    assert caught.value.code == "invalid_profile_response"
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "value",
    [None, "", "  ", 7, "SYNTHETIC_NULL", "SYNTHETIC_MISSING", "SYNTHETIC_EMPTY"],
)
def test_unavailable_name_keeps_industry_and_reports_missing(value: object) -> None:
    result = parse(
        (
            row(
                A股代码="000000",
                A股简称=value,
                所属行业="SYNTHETIC_INDUSTRY",
            ),
        )
    )

    assert result.items[0].name is None
    assert result.items[0].industry == "SYNTHETIC_INDUSTRY"
    assert [issue.code for issue in result.issues] == ["missing_profile_fields"]
    assert result.issues[0].message.endswith("name")


@pytest.mark.parametrize(
    "value",
    [None, "", "  ", 7, "SYNTHETIC_NULL", "SYNTHETIC_MISSING", "SYNTHETIC_EMPTY"],
)
def test_unavailable_industry_keeps_name_and_reports_missing(value: object) -> None:
    result = parse(
        (
            row(
                A股代码="000000",
                A股简称="SYNTHETIC_NAME",
                所属行业=value,
            ),
        )
    )

    assert result.items[0].name == "SYNTHETIC_NAME"
    assert result.items[0].industry is None
    assert [issue.code for issue in result.issues] == ["missing_profile_fields"]
    assert result.issues[0].message.endswith("industry")


def test_missing_candidate_columns_do_not_backfill_request_name() -> None:
    result = parse((row(A股代码="000000"),))

    assert result.items == ()
    assert [issue.code for issue in result.issues] == ["profile_not_found"]


def test_unknown_and_dynamic_columns_are_ignored() -> None:
    result = parse(
        (
            row(
                A股代码="000000",
                A股简称="SYNTHETIC_NAME",
                所属行业="SYNTHETIC_INDUSTRY",
                机构简介="SYNTHETIC_FREE_TEXT",
                主营业务="SYNTHETIC_FREE_TEXT",
                经营范围="SYNTHETIC_FREE_TEXT",
                总市值=123,
                价格=456,
                股本=789,
                UNKNOWN_COLUMN="SYNTHETIC_UNKNOWN",
            ),
        )
    )

    profile = result.items[0]
    assert profile.name == "SYNTHETIC_NAME"
    assert profile.industry == "SYNTHETIC_INDUSTRY"
    assert profile.description is None
    assert profile.exchange is None
    assert profile.currency is None


def test_matching_identity_is_trimmed_and_verified() -> None:
    result = parse(
        (
            row(
                A股代码=" 000000 ",
                A股简称=" SYNTHETIC_NAME ",
                所属行业=" SYNTHETIC_INDUSTRY ",
            ),
        )
    )

    assert result.items[0].name == "SYNTHETIC_NAME"
    assert result.items[0].industry == "SYNTHETIC_INDUSTRY"
    assert result.issues == ()


def test_missing_identity_keeps_profile_with_unverified_issue() -> None:
    result = parse(
        (
            row(
                A股简称="SYNTHETIC_NAME",
                所属行业="SYNTHETIC_INDUSTRY",
            ),
        )
    )

    assert result.items[0].symbol == "000000"
    assert [(issue.category, issue.code) for issue in result.issues] == [
        (IssueCategory.VALIDATION, "profile_identity_unverified")
    ]


def test_missing_identity_and_profile_fields_report_both_safe_issues() -> None:
    result = parse((row(UNKNOWN_COLUMN="SYNTHETIC_PRIVATE_VALUE"),))

    assert result.items == ()
    assert [issue.code for issue in result.issues] == [
        "profile_identity_unverified",
        "profile_not_found",
    ]
    assert "SYNTHETIC_PRIVATE_VALUE" not in " ".join(
        issue.message for issue in result.issues
    )


def test_conflicting_identity_raises_safe_validation_error() -> None:
    with pytest.raises(ProviderValidationError) as caught:
        parse(
            (
                row(
                    A股代码="999999",
                    A股简称="SYNTHETIC_PRIVATE_NAME",
                    所属行业="SYNTHETIC_PRIVATE_INDUSTRY",
                ),
            )
        )

    assert caught.value.code == "profile_identity_mismatch"
    assert caught.value.provider_id == "cninfo"
    assert "999999" not in caught.value.safe_message
    assert "SYNTHETIC" not in caught.value.safe_message


@pytest.mark.parametrize("value", [1, True, None, "", "12345", "1234567", "ABC123", "００００００"])
def test_invalid_identity_type_or_format_raises_safe_parse_error(value: object) -> None:
    with pytest.raises(ProviderParseError) as caught:
        parse((row(A股代码=value, A股简称="SYNTHETIC_PRIVATE_NAME"),))

    assert caught.value.code == "invalid_profile_identity"
    assert caught.value.__cause__ is not None
    assert "SYNTHETIC_PRIVATE_NAME" not in caught.value.safe_message


@pytest.mark.parametrize(
    "market_column",
    [None, "SYNTHETIC_UNKNOWN_MARKET", "cn", "SSE", "ARBITRARY"],
)
def test_market_candidate_never_overrides_standard_identity(
    market_column: object,
) -> None:
    values = {
        "A股代码": "000000",
        "A股简称": "SYNTHETIC_NAME",
        "所属行业": "SYNTHETIC_INDUSTRY",
    }
    if market_column is not None:
        values["所属市场"] = market_column

    profile = parse((MappingProxyType(values),)).items[0]

    assert profile.market == "cn"
    assert profile.exchange is None


def test_issues_do_not_contain_synthetic_profile_values() -> None:
    result = parse(
        (
            row(
                A股代码="000000",
                A股简称="SYNTHETIC_PRIVATE_NAME",
                所属行业=None,
                所属市场="SYNTHETIC_PRIVATE_MARKET",
            ),
        )
    )

    messages = " ".join(issue.message for issue in result.issues)
    assert "SYNTHETIC_PRIVATE_NAME" not in messages
    assert "SYNTHETIC_PRIVATE_MARKET" not in messages


def test_parser_requires_valid_cn_security_and_aware_datetime() -> None:
    cases = [
        (
            object(),
            NOW,
            "invalid_security",
        ),
        (
            Security("us", "AAPL", "SYNTHETIC"),
            NOW,
            "unsupported_market",
        ),
        (
            Security("cn", "ABC123", "SYNTHETIC"),
            NOW,
            "invalid_symbol",
        ),
        (
            SECURITY,
            datetime(2026, 7, 28),
            "invalid_fetched_at",
        ),
        (
            SECURITY,
            "SYNTHETIC_NOT_A_DATETIME",
            "invalid_fetched_at",
        ),
    ]
    for security, fetched_at, code in cases:
        with pytest.raises(ProviderValidationError) as caught:
            parse_cninfo_profile_rows(
                (),
                security=security,  # type: ignore[arg-type]
                fetched_at=fetched_at,  # type: ignore[arg-type]
            )
        assert caught.value.code == code
