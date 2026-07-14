from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from daily_report_agent.models.issues import IssueCategory, IssueSeverity
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.security import Security
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.errors import (
    ProviderBlockedError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderValidationError,
)
from daily_report_agent.providers.tencent.quote import TencentQuoteProvider
from daily_report_agent.providers.tencent.transport import (
    TencentTransportBlockedError,
    TencentTransportRateLimitError,
)


NOW = datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)


@dataclass
class FakeTransport:
    outcomes: list[object]
    calls: list[tuple[tuple[str, ...], float]] = field(default_factory=list)

    def fetch_quote_text(
        self,
        symbols: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> str:
        self.calls.append((symbols, timeout_seconds))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, str)
        return outcome


def cn(symbol: str) -> Security:
    return Security("cn", symbol, "Test")


def test_constructor_and_empty_input_do_not_call_transport() -> None:
    transport = FakeTransport([])
    provider = TencentQuoteProvider(transport, clock=lambda: NOW)

    result = provider.fetch_quotes(())

    assert result == ProviderResult(provider=provider.descriptor)
    assert transport.calls == []


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"timeout_seconds": 0}, ValueError),
        ({"timeout_seconds": float("inf")}, ValueError),
        ({"batch_size": 0}, ValueError),
        ({"batch_size": True}, ValueError),
        ({"clock": None}, TypeError),
    ],
)
def test_constructor_validates_options(kwargs: dict[str, object], error_type: type[Exception]) -> None:
    with pytest.raises(error_type):
        TencentQuoteProvider(FakeTransport([]), **kwargs)  # type: ignore[arg-type]


def test_single_batch_calls_transport_once_with_mapped_symbols(
    tencent_fixture,
) -> None:
    transport = FakeTransport([tencent_fixture("quote_batch_mixed.txt")])
    provider = TencentQuoteProvider(
        transport,
        timeout_seconds=3.5,
        batch_size=10,
        clock=lambda: NOW,
    )

    result = provider.fetch_quotes((cn("600000"), cn("300750")))

    assert transport.calls == [(('sh600000', 'sz300750'), 3.5)]
    assert isinstance(result, ProviderResult)
    assert tuple(item.symbol for item in result.items) == ("600000", "300750")
    assert all(isinstance(item, MarketSnapshot) for item in result.items)


def test_multiple_batches_preserve_input_order_and_merge_results(
    tencent_fixture,
) -> None:
    transport = FakeTransport(
        [
            tencent_fixture("quote_single_sh.txt"),
            tencent_fixture("quote_single_sz.txt"),
            tencent_fixture("quote_suspended_or_partial.txt"),
        ]
    )
    provider = TencentQuoteProvider(transport, batch_size=1, clock=lambda: NOW)

    result = provider.fetch_quotes((cn("600000"), cn("000001"), cn("688981")))

    assert [call[0] for call in transport.calls] == [
        ("sh600000",),
        ("sz000001",),
        ("sh688981",),
    ]
    assert tuple(item.symbol for item in result.items) == (
        "600000",
        "000001",
        "688981",
    )
    assert any(issue.code == "quote_time_fallback" for issue in result.issues)


@pytest.mark.parametrize(
    ("cause", "error_type", "retryable"),
    [
        (TimeoutError("Bearer timeout-secret"), ProviderTimeoutError, True),
        (ConnectionError("https://secret.test?q=key"), ProviderNetworkError, True),
        (TencentTransportBlockedError("api_key=secret"), ProviderBlockedError, True),
        (TencentTransportRateLimitError("token=secret"), ProviderRateLimitError, True),
    ],
)
def test_single_batch_transport_failure_raises_safe_provider_error(
    cause: Exception,
    error_type: type[Exception],
    retryable: bool,
) -> None:
    provider = TencentQuoteProvider(FakeTransport([cause]), clock=lambda: NOW)

    with pytest.raises(error_type) as caught:
        provider.fetch_quotes((cn("600000"),))

    error = caught.value
    assert error.provider_id == "tencent-finance"
    assert error.operation == "fetch_quotes"
    assert error.retryable is retryable
    assert error.__cause__ is cause
    assert "secret" not in error.safe_message.lower()
    assert "http" not in error.safe_message.lower()


def test_single_batch_malformed_response_raises_parse_error(tencent_fixture) -> None:
    provider = TencentQuoteProvider(
        FakeTransport([tencent_fixture("quote_all_malformed.txt")]),
        clock=lambda: NOW,
    )
    with pytest.raises(ProviderParseError) as caught:
        provider.fetch_quotes((cn("000001"),))
    assert caught.value.__cause__ is not None
    assert "NOT_A_NUMBER" not in caught.value.safe_message


def test_partial_batch_failure_returns_data_and_error_issue(tencent_fixture) -> None:
    cause = TimeoutError("Bearer secret-token")
    provider = TencentQuoteProvider(
        FakeTransport([tencent_fixture("quote_single_sh.txt"), cause]),
        batch_size=1,
        clock=lambda: NOW,
    )

    result = provider.fetch_quotes((cn("600000"), cn("000001")))

    assert tuple(item.symbol for item in result.items) == ("600000",)
    error_issue = next(issue for issue in result.issues if issue.severity is IssueSeverity.ERROR)
    assert error_issue.category is IssueCategory.NETWORK
    assert error_issue.provider == "tencent-finance"
    assert error_issue.retryable is True
    assert "secret-token" not in error_issue.message


def test_all_batches_failed_raises_first_safe_error() -> None:
    first = TimeoutError("first secret")
    second = ConnectionError("second secret")
    provider = TencentQuoteProvider(
        FakeTransport([first, second]),
        batch_size=1,
        clock=lambda: NOW,
    )
    with pytest.raises(ProviderTimeoutError) as caught:
        provider.fetch_quotes((cn("600000"), cn("000001")))
    assert caught.value.__cause__ is first


def test_input_and_clock_validation_happen_without_network() -> None:
    transport = FakeTransport([])
    provider = TencentQuoteProvider(
        transport,
        clock=lambda: datetime(2026, 7, 14),
    )
    with pytest.raises(ProviderValidationError):
        provider.fetch_quotes((cn("600000"),))
    with pytest.raises(ProviderValidationError):
        provider.fetch_quotes([cn("600000")])  # type: ignore[arg-type]
    assert transport.calls == []
