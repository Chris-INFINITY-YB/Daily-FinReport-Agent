from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import (
    ProviderBlockedError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from daily_report_agent.providers.eastmoney.news import EastmoneyNewsProvider
from daily_report_agent.providers.eastmoney.transport import (
    EastmoneyTransportBlockedError,
    EastmoneyTransportRateLimitError,
)


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SECURITY = Security("cn", "123456", "Synthetic Security")


def row(title: str, published_at: str, **extra: object):
    return MappingProxyType(
        {
            "新闻标题": title,
            "新闻内容": f"{title} SUMMARY",
            "发布时间": published_at,
            "新闻链接": f"https://example.invalid/{title.lower().replace(' ', '-')}",
            **extra,
        }
    )


def rows():
    return (
        row("FIRST", "2026-07-18 09:30:00"),
        row("SECOND", "2026-07-18 09:30:00"),
        row("NEWEST", "2026-07-18 10:00:00"),
        row("BOUNDARY", "2026-07-18 08:00:00"),
        row("OUTSIDE", "2026-07-17 23:59:59"),
    )


@dataclass
class FakeTransport:
    outcome: object
    calls: list[str] = field(default_factory=list)

    def fetch_news_rows(self, symbol: str):
        self.calls.append(symbol)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@dataclass
class Clock:
    value: object
    calls: int = 0

    def __call__(self):
        self.calls += 1
        return self.value


def test_fetch_calls_clock_and_transport_once_and_reuses_fetched_at() -> None:
    transport = FakeTransport(rows())
    clock = Clock(NOW)
    provider = EastmoneyNewsProvider(transport, clock=clock)

    result = provider.fetch_news(
        SECURITY,
        datetime(2026, 7, 18, 0, 0, tzinfo=SHANGHAI),
        datetime(2026, 7, 18, 23, 59, 59, tzinfo=SHANGHAI),
        10,
    )

    assert clock.calls == 1
    assert transport.calls == ["123456"]
    assert len(result.items) == 4
    assert all(item.fetched_at is NOW for item in result.items)


def test_closed_window_descending_stable_sort_then_limit() -> None:
    provider = EastmoneyNewsProvider(FakeTransport(rows()), clock=lambda: NOW)

    result = provider.fetch_news(
        SECURITY,
        datetime(2026, 7, 18, 8, 0, tzinfo=SHANGHAI),
        datetime(2026, 7, 18, 10, 0, tzinfo=SHANGHAI),
        3,
    )

    assert [item.title for item in result.items] == ["NEWEST", "FIRST", "SECOND"]


def test_window_comparison_respects_equivalent_instants_across_timezones() -> None:
    provider = EastmoneyNewsProvider(
        FakeTransport((row("BOUNDARY", "2026-07-18 08:00:00"),)),
        clock=lambda: NOW,
    )
    instant = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)

    result = provider.fetch_news(SECURITY, instant, instant, 1)

    assert [item.title for item in result.items] == ["BOUNDARY"]


def test_filtered_empty_is_success_with_safe_info_issue() -> None:
    provider = EastmoneyNewsProvider(FakeTransport(rows()), clock=lambda: NOW)

    result = provider.fetch_news(
        SECURITY,
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 2, tzinfo=timezone.utc),
        2,
    )

    assert result.items == ()
    assert result.issues[-1].code == "news_not_found"


def test_empty_transport_result_is_success_not_failure() -> None:
    result = EastmoneyNewsProvider(FakeTransport(()), clock=lambda: NOW).fetch_news(
        SECURITY,
        NOW - timedelta(days=1),
        NOW,
        1,
    )

    assert result.items == ()
    assert [issue.code for issue in result.issues] == ["news_not_found"]


@pytest.mark.parametrize(
    ("security", "code"),
    [
        (Security("us", "AAPL", "Apple"), "unsupported_market"),
        (Security("cn", "ABC123", "Invalid"), "invalid_symbol"),
        (Security("cn", "12345", "Invalid"), "invalid_symbol"),
        (Security("cn", "１２３４５６", "Invalid"), "invalid_symbol"),
    ],
)
def test_invalid_security_fails_before_clock_and_transport(
    security: Security,
    code: str,
) -> None:
    transport = FakeTransport(rows())
    clock = Clock(NOW)

    with pytest.raises(ProviderValidationError) as caught:
        EastmoneyNewsProvider(transport, clock=clock).fetch_news(
            security, NOW, NOW, 1
        )

    assert caught.value.code == code
    assert clock.calls == 0
    assert transport.calls == []


def test_non_security_input_fails_before_clock_and_transport() -> None:
    transport = FakeTransport(rows())
    clock = Clock(NOW)

    with pytest.raises(ProviderValidationError) as caught:
        EastmoneyNewsProvider(transport, clock=clock).fetch_news(
            object(),  # type: ignore[arg-type]
            NOW,
            NOW,
            1,
        )

    assert caught.value.code == "invalid_security"
    assert clock.calls == 0
    assert transport.calls == []


def test_clock_must_be_callable_at_construction() -> None:
    with pytest.raises(TypeError, match="clock"):
        EastmoneyNewsProvider(FakeTransport(rows()), clock=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("since", "until", "limit", "code"),
    [
        (datetime(2026, 7, 18), NOW, 1, "invalid_since"),
        (NOW, datetime(2026, 7, 18), 1, "invalid_until"),
        (NOW, NOW - timedelta(seconds=1), 1, "invalid_time_window"),
        (NOW, NOW, 0, "invalid_limit"),
        (NOW, NOW, -1, "invalid_limit"),
        (NOW, NOW, True, "invalid_limit"),
        (NOW, NOW, 1.0, "invalid_limit"),
    ],
)
def test_invalid_window_or_limit_fails_before_clock_and_transport(
    since: object,
    until: object,
    limit: object,
    code: str,
) -> None:
    transport = FakeTransport(rows())
    clock = Clock(NOW)

    with pytest.raises(ProviderValidationError) as caught:
        EastmoneyNewsProvider(transport, clock=clock).fetch_news(
            SECURITY,
            since,  # type: ignore[arg-type]
            until,  # type: ignore[arg-type]
            limit,  # type: ignore[arg-type]
        )

    assert caught.value.code == code
    assert clock.calls == 0
    assert transport.calls == []


@pytest.mark.parametrize("clock_value", [datetime(2026, 7, 18), "not-a-time", None])
def test_invalid_clock_fails_before_transport(clock_value: object) -> None:
    transport = FakeTransport(rows())
    clock = Clock(clock_value)

    with pytest.raises(ProviderValidationError) as caught:
        EastmoneyNewsProvider(transport, clock=clock).fetch_news(
            SECURITY, NOW, NOW, 1
        )

    assert caught.value.code == "invalid_clock"
    assert clock.calls == 1
    assert transport.calls == []


def test_default_clock_returns_aware_utc_time() -> None:
    result = EastmoneyNewsProvider(
        FakeTransport((row("SYNTHETIC", "2026-07-18T08:00:00Z"),))
    ).fetch_news(
        SECURITY,
        datetime(2026, 7, 18, tzinfo=timezone.utc),
        datetime(2026, 7, 19, tzinfo=timezone.utc),
        1,
    )

    assert result.items[0].fetched_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("cause", "error_type", "code"),
    [
        (TimeoutError("Bearer timeout-secret"), ProviderTimeoutError, "transport_timeout"),
        (ConnectionError("https://unsafe.invalid/?token=secret"), ProviderNetworkError, "transport_network"),
        (EastmoneyTransportRateLimitError("api_key=secret"), ProviderRateLimitError, "transport_rate_limit"),
        (EastmoneyTransportBlockedError("Cookie: secret"), ProviderBlockedError, "transport_blocked"),
        (RuntimeError("private response https://unsafe.invalid"), ProviderUnavailableError, "transport_unavailable"),
    ],
)
def test_transport_errors_are_mapped_without_sensitive_text(
    cause: Exception,
    error_type: type[Exception],
    code: str,
) -> None:
    provider = EastmoneyNewsProvider(FakeTransport(cause), clock=lambda: NOW)

    with pytest.raises(error_type) as caught:
        provider.fetch_news(SECURITY, NOW, NOW, 1)

    error = caught.value
    assert error.provider_id == "eastmoney"
    assert error.operation == "fetch_news"
    assert error.code == code
    assert error.__cause__ is cause
    rendered = f"{error.safe_message} {error}"
    for forbidden in ("secret", "unsafe.invalid", "token", "cookie", "bearer"):
        assert forbidden not in rendered.lower()


def test_existing_provider_error_is_propagated_unchanged() -> None:
    cause = ProviderParseError(
        provider_id="eastmoney",
        operation="fetch_news",
        safe_message="Safe parse failure",
        code="fixture_parse_failure",
    )

    with pytest.raises(ProviderParseError) as caught:
        EastmoneyNewsProvider(FakeTransport(cause), clock=lambda: NOW).fetch_news(
            SECURITY, NOW, NOW, 1
        )

    assert caught.value is cause


def test_malformed_top_level_from_transport_raises_safe_parse_error() -> None:
    provider = EastmoneyNewsProvider(FakeTransport([]), clock=lambda: NOW)

    with pytest.raises(ProviderParseError) as caught:
        provider.fetch_news(SECURITY, NOW, NOW, 1)

    assert caught.value.code == "invalid_news_response"
    assert caught.value.__cause__ is not None
