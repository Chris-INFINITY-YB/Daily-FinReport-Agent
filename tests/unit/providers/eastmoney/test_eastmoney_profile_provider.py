from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

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
from daily_report_agent.providers.eastmoney.profile import (
    EastmoneyProfileProvider,
)
from daily_report_agent.providers.eastmoney.transport import (
    EastmoneyTransportBlockedError,
    EastmoneyTransportRateLimitError,
)


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def rows():
    return (
        MappingProxyType({"item": "股票简称", "value": "贵州茅台"}),
        MappingProxyType({"item": "行业", "value": "白酒"}),
    )


@dataclass
class FakeTransport:
    outcome: object
    calls: list[str] = field(default_factory=list)

    def fetch_profile_rows(self, symbol: str):
        self.calls.append(symbol)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def test_transport_is_required_and_construction_does_not_call_it() -> None:
    with pytest.raises(TypeError):
        EastmoneyProfileProvider()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="transport"):
        EastmoneyProfileProvider(None)  # type: ignore[arg-type]

    transport = FakeTransport(rows())
    EastmoneyProfileProvider(transport, clock=lambda: NOW)
    assert transport.calls == []


def test_fetch_calls_transport_once_with_normalized_symbol() -> None:
    transport = FakeTransport(rows())
    provider = EastmoneyProfileProvider(transport, clock=lambda: NOW)

    result = provider.fetch_profile(Security("cn", " 600519 ", "贵州茅台"))

    assert transport.calls == ["600519"]
    assert result.items[0].symbol == "600519"
    assert result.items[0].fetched_at is NOW


def test_default_clock_returns_aware_utc_time() -> None:
    result = EastmoneyProfileProvider(FakeTransport(rows())).fetch_profile(
        Security("cn", "600519", "贵州茅台")
    )

    assert result.items[0].fetched_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("security", "code"),
    [
        (Security("us", "AAPL", "Apple"), "unsupported_market"),
        (Security("cn", "ABC123", "Invalid"), "invalid_symbol"),
        (Security("cn", "60000", "Invalid"), "invalid_symbol"),
        (Security("cn", "６００５１９", "Invalid"), "invalid_symbol"),
    ],
)
def test_invalid_input_is_rejected_before_transport(
    security: Security,
    code: str,
) -> None:
    transport = FakeTransport(rows())
    provider = EastmoneyProfileProvider(transport, clock=lambda: NOW)

    with pytest.raises(ProviderValidationError) as caught:
        provider.fetch_profile(security)

    assert caught.value.code == code
    assert transport.calls == []


def test_naive_clock_is_rejected_before_transport() -> None:
    transport = FakeTransport(rows())
    provider = EastmoneyProfileProvider(
        transport,
        clock=lambda: datetime(2026, 7, 18),
    )

    with pytest.raises(ProviderValidationError) as caught:
        provider.fetch_profile(Security("cn", "600519", "贵州茅台"))

    assert caught.value.code == "invalid_clock"
    assert transport.calls == []


@pytest.mark.parametrize(
    ("cause", "error_type", "code", "retryable"),
    [
        (
            TimeoutError("Bearer timeout-secret"),
            ProviderTimeoutError,
            "transport_timeout",
            True,
        ),
        (
            ConnectionError("https://secret.test/?token=secret"),
            ProviderNetworkError,
            "transport_network",
            True,
        ),
        (
            EastmoneyTransportRateLimitError("api_key=secret"),
            ProviderRateLimitError,
            "transport_rate_limit",
            True,
        ),
        (
            EastmoneyTransportBlockedError("token=secret"),
            ProviderBlockedError,
            "transport_blocked",
            True,
        ),
        (
            RuntimeError("private response at https://secret.test"),
            ProviderUnavailableError,
            "transport_unavailable",
            True,
        ),
    ],
)
def test_transport_failure_maps_to_safe_provider_error(
    cause: Exception,
    error_type: type[Exception],
    code: str,
    retryable: bool,
) -> None:
    provider = EastmoneyProfileProvider(FakeTransport(cause), clock=lambda: NOW)

    with pytest.raises(error_type) as caught:
        provider.fetch_profile(Security("cn", "600519", "贵州茅台"))

    error = caught.value
    assert error.provider_id == "eastmoney"
    assert error.operation == "fetch_profile"
    assert error.code == code
    assert error.retryable is retryable
    assert error.__cause__ is cause
    assert "secret" not in error.safe_message.lower()
    assert "http" not in error.safe_message.lower()


def test_malformed_transport_rows_propagate_safe_parse_error() -> None:
    provider = EastmoneyProfileProvider(
        FakeTransport((MappingProxyType({"unexpected": "value"}),)),
        clock=lambda: NOW,
    )

    with pytest.raises(ProviderParseError) as caught:
        provider.fetch_profile(Security("cn", "600519", "贵州茅台"))

    assert caught.value.code == "invalid_profile_response"
    assert caught.value.__cause__ is not None
