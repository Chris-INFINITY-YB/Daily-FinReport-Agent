from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from daily_report_agent.models.security import Security
from daily_report_agent.providers.cninfo.profile import CninfoProfileProvider
from daily_report_agent.providers.cninfo.transport import (
    CninfoTransportBlockedError,
    CninfoTransportRateLimitError,
)
from daily_report_agent.providers.errors import (
    ProviderBlockedError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


def rows():
    return (
        MappingProxyType(
            {
                "A股代码": "000000",
                "A股简称": "SYNTHETIC_NAME",
                "所属行业": "SYNTHETIC_INDUSTRY",
            }
        ),
    )


@dataclass
class SyntheticTransport:
    outcome: object
    calls: list[str] = field(default_factory=list)

    def fetch_profile_rows(self, symbol: str):
        self.calls.append(symbol)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def test_transport_is_required_and_construction_does_not_call_it() -> None:
    with pytest.raises(TypeError):
        CninfoProfileProvider()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="transport"):
        CninfoProfileProvider(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="clock"):
        CninfoProfileProvider(SyntheticTransport(rows()), clock=None)  # type: ignore[arg-type]

    transport = SyntheticTransport(rows())
    CninfoProfileProvider(transport, clock=lambda: NOW)
    assert transport.calls == []


def test_fetch_calls_transport_once_with_normalized_symbol() -> None:
    transport = SyntheticTransport(rows())
    provider = CninfoProfileProvider(transport, clock=lambda: NOW)

    result = provider.fetch_profile(
        Security("cn", " 000000 ", "SYNTHETIC_REQUEST_NAME")
    )

    assert transport.calls == ["000000"]
    assert result.items[0].symbol == "000000"
    assert result.items[0].fetched_at is NOW


def test_default_clock_returns_aware_utc_time() -> None:
    result = CninfoProfileProvider(SyntheticTransport(rows())).fetch_profile(
        Security("cn", "000000", "SYNTHETIC_REQUEST_NAME")
    )

    assert result.items[0].fetched_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("security", "code"),
    [
        (object(), "invalid_security"),
        (Security("us", "AAPL", "SYNTHETIC"), "unsupported_market"),
        (Security("cn", "ABC123", "SYNTHETIC"), "invalid_symbol"),
        (Security("cn", "00000", "SYNTHETIC"), "invalid_symbol"),
        (Security("cn", "００００００", "SYNTHETIC"), "invalid_symbol"),
    ],
)
def test_invalid_input_is_rejected_before_transport(
    security: object,
    code: str,
) -> None:
    transport = SyntheticTransport(rows())
    provider = CninfoProfileProvider(transport, clock=lambda: NOW)

    with pytest.raises(ProviderValidationError) as caught:
        provider.fetch_profile(security)  # type: ignore[arg-type]

    assert caught.value.code == code
    assert transport.calls == []


@pytest.mark.parametrize(
    "clock_value",
    [datetime(2026, 7, 28), "SYNTHETIC_NOT_A_DATETIME", None],
)
def test_invalid_clock_is_rejected_before_transport(clock_value: object) -> None:
    transport = SyntheticTransport(rows())
    provider = CninfoProfileProvider(
        transport,
        clock=lambda: clock_value,  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(ProviderValidationError) as caught:
        provider.fetch_profile(
            Security("cn", "000000", "SYNTHETIC_REQUEST_NAME")
        )

    assert caught.value.code == "invalid_clock"
    assert transport.calls == []


@pytest.mark.parametrize(
    ("cause", "error_type", "code", "retryable"),
    [
        (
            TimeoutError("Bearer synthetic-timeout-secret"),
            ProviderTimeoutError,
            "transport_timeout",
            True,
        ),
        (
            ConnectionError("https://synthetic.invalid/?token=secret"),
            ProviderNetworkError,
            "transport_network",
            True,
        ),
        (
            CninfoTransportRateLimitError("api_key=synthetic-secret"),
            ProviderRateLimitError,
            "transport_rate_limit",
            True,
        ),
        (
            CninfoTransportBlockedError("token=synthetic-secret"),
            ProviderBlockedError,
            "transport_blocked",
            True,
        ),
        (
            RuntimeError("synthetic response at https://synthetic.invalid"),
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
    transport = SyntheticTransport(cause)
    provider = CninfoProfileProvider(transport, clock=lambda: NOW)

    with pytest.raises(error_type) as caught:
        provider.fetch_profile(
            Security("cn", "000000", "SYNTHETIC_REQUEST_NAME")
        )

    error = caught.value
    assert transport.calls == ["000000"]
    assert error.provider_id == "cninfo"
    assert error.operation == "fetch_profile"
    assert error.code == code
    assert error.retryable is retryable
    assert error.__cause__ is cause
    assert "secret" not in error.safe_message.lower()
    assert "http" not in error.safe_message.lower()
    assert "synthetic_request_name" not in error.safe_message.lower()


def test_malformed_rows_propagate_parse_error_after_one_call() -> None:
    transport = SyntheticTransport((rows()[0], rows()[0]))
    provider = CninfoProfileProvider(transport, clock=lambda: NOW)

    with pytest.raises(ProviderParseError) as caught:
        provider.fetch_profile(
            Security("cn", "000000", "SYNTHETIC_REQUEST_NAME")
        )

    assert transport.calls == ["000000"]
    assert caught.value.code == "invalid_profile_response"
    assert caught.value.__cause__ is not None


def test_identity_conflict_is_not_rewritten_as_transport_error() -> None:
    transport = SyntheticTransport(
        (
            MappingProxyType(
                {
                    "A股代码": "999999",
                    "A股简称": "SYNTHETIC_PRIVATE_NAME",
                }
            ),
        )
    )
    provider = CninfoProfileProvider(transport, clock=lambda: NOW)

    with pytest.raises(ProviderValidationError) as caught:
        provider.fetch_profile(
            Security("cn", "000000", "SYNTHETIC_REQUEST_NAME")
        )

    assert transport.calls == ["000000"]
    assert caught.value.code == "profile_identity_mismatch"
