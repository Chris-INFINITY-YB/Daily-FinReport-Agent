from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daily_report_agent.models.issues import IssueCategory, IssueSeverity
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import (
    ProviderParseError,
    ProviderValidationError,
)
from daily_report_agent.providers.tencent.parser import parse_tencent_quote_response


FETCHED_AT = datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)


def security(symbol: str, name: str = "Test") -> Security:
    return Security("cn", symbol, name)


@pytest.mark.parametrize(
    ("fixture_name", "symbol", "price", "previous_close", "pct_change", "hour"),
    [
        ("quote_single_sh.txt", "600000", 10.25, 10.0, 2.5, 15),
        ("quote_single_sz.txt", "000001", 12.34, 12.2, 1.15, 14),
    ],
)
def test_parses_single_quote_into_standard_snapshot(
    tencent_fixture,
    fixture_name: str,
    symbol: str,
    price: float,
    previous_close: float,
    pct_change: float,
    hour: int,
) -> None:
    result = parse_tencent_quote_response(
        tencent_fixture(fixture_name),
        requested=(security(symbol),),
        fetched_at=FETCHED_AT,
    )

    assert result.provider.provider_id == "tencent-finance"
    assert result.issues == ()
    assert len(result.items) == 1
    item = result.items[0]
    assert isinstance(item, MarketSnapshot)
    assert item.symbol == symbol
    assert item.source == "tencent-finance"
    assert item.currency == "CNY"
    assert item.price == price
    assert item.previous_close == previous_close
    assert item.pct_change == pct_change
    assert item.observed_at.hour == hour
    assert item.observed_at.utcoffset() == timedelta(hours=8)


def test_parser_matches_response_codes_and_returns_requested_order(
    tencent_fixture,
) -> None:
    result = parse_tencent_quote_response(
        tencent_fixture("quote_batch_mixed.txt"),
        requested=(security("600000"), security("300750")),
        fetched_at=FETCHED_AT,
    )
    assert tuple(item.symbol for item in result.items) == ("600000", "300750")
    assert tuple(item.price for item in result.items) == (10.25, 205.6)


def test_real_zero_is_not_treated_as_missing(tencent_fixture) -> None:
    result = parse_tencent_quote_response(
        tencent_fixture("quote_zero_change.txt"),
        requested=(security("002594"),),
        fetched_at=FETCHED_AT,
    )
    assert result.items[0].pct_change == 0.0


def test_partial_record_keeps_none_and_uses_aware_fetched_at(
    tencent_fixture,
) -> None:
    result = parse_tencent_quote_response(
        tencent_fixture("quote_suspended_or_partial.txt"),
        requested=(security("688981"),),
        fetched_at=FETCHED_AT,
    )
    item = result.items[0]
    assert item.price == 45.6
    assert item.previous_close is None
    assert item.pct_change is None
    assert item.volume is None
    assert item.amount is None
    assert item.turnover is None
    assert item.pe_ttm is None
    assert item.pb is None
    assert item.market_cap is None
    assert item.observed_at is FETCHED_AT
    assert [(issue.category, issue.code) for issue in result.issues] == [
        (IssueCategory.MISSING_DATA, "quote_time_fallback")
    ]


def test_explicit_empty_record_is_success_without_data(tencent_fixture) -> None:
    result = parse_tencent_quote_response(
        tencent_fixture("quote_empty.txt"),
        requested=(security("600000"),),
        fetched_at=FETCHED_AT,
    )
    assert result.items == ()
    assert result.issues == ()


def test_partial_malformed_response_preserves_valid_record(tencent_fixture) -> None:
    result = parse_tencent_quote_response(
        tencent_fixture("quote_malformed.txt"),
        requested=(security("600000"), security("000001")),
        fetched_at=FETCHED_AT,
    )
    assert tuple(item.symbol for item in result.items) == ("600000",)
    assert any(issue.category is IssueCategory.PARSE for issue in result.issues)
    assert any(issue.code == "missing_requested_symbol" for issue in result.issues)
    assert all(issue.severity is IssueSeverity.WARNING for issue in result.issues)


@pytest.mark.parametrize("fixture_name", ["quote_all_malformed.txt"])
def test_all_malformed_response_raises_safe_parse_error(
    tencent_fixture,
    fixture_name: str,
) -> None:
    with pytest.raises(ProviderParseError) as caught:
        parse_tencent_quote_response(
            tencent_fixture(fixture_name),
            requested=(security("000001"),),
            fetched_at=FETCHED_AT,
        )
    assert caught.value.provider_id == "tencent-finance"
    assert caught.value.code == "no_valid_records"
    assert caught.value.__cause__ is not None
    assert "NOT_A_NUMBER" not in caught.value.safe_message


def test_empty_unrecognizable_response_is_a_parse_error() -> None:
    with pytest.raises(ProviderParseError) as caught:
        parse_tencent_quote_response(
            "",
            requested=(security("600000"),),
            fetched_at=FETCHED_AT,
        )
    assert caught.value.code == "empty_response"


def test_unrequested_record_is_not_returned(tencent_fixture) -> None:
    text = tencent_fixture("quote_batch_mixed.txt")
    result = parse_tencent_quote_response(
        text,
        requested=(security("600000"),),
        fetched_at=FETCHED_AT,
    )
    assert tuple(item.symbol for item in result.items) == ("600000",)
    assert any(issue.code == "unrequested_symbol" for issue in result.issues)


def test_missing_requested_record_produces_issue(tencent_fixture) -> None:
    result = parse_tencent_quote_response(
        tencent_fixture("quote_single_sh.txt"),
        requested=(security("600000"), security("000001")),
        fetched_at=FETCHED_AT,
    )
    assert tuple(item.symbol for item in result.items) == ("600000",)
    assert any(issue.code == "missing_requested_symbol" for issue in result.issues)


def test_duplicate_response_record_is_ignored_with_issue(tencent_fixture) -> None:
    line = tencent_fixture("quote_single_sh.txt").strip()
    result = parse_tencent_quote_response(
        f"{line}\n{line}",
        requested=(security("600000"),),
        fetched_at=FETCHED_AT,
    )
    assert len(result.items) == 1
    assert any(issue.code == "duplicate_record" for issue in result.issues)


def test_duplicate_requested_symbol_and_naive_time_are_rejected(
    tencent_fixture,
) -> None:
    duplicate = (security("600000"), security("600000"))
    with pytest.raises(ProviderValidationError):
        parse_tencent_quote_response(
            tencent_fixture("quote_single_sh.txt"),
            requested=duplicate,
            fetched_at=FETCHED_AT,
        )
    with pytest.raises(ProviderValidationError, match="timezone-aware"):
        parse_tencent_quote_response(
            tencent_fixture("quote_single_sh.txt"),
            requested=(security("600000"),),
            fetched_at=datetime(2026, 7, 14),
        )
