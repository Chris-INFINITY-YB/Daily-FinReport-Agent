"""CNInfo CN Profile Provider；仅支持显式注入的 Transport。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone

from daily_report_agent.models.news import is_timezone_aware
from daily_report_agent.models.profile import SecurityProfile
from daily_report_agent.models.security import Security
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.errors import (
    ProviderBlockedError,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)

from .constants import CNINFO_PROFILE_DESCRIPTOR
from .parser import parse_cninfo_profile_rows
from .transport import (
    CninfoProfileTransport,
    CninfoTransportBlockedError,
    CninfoTransportRateLimitError,
)


_CN_SYMBOL_PATTERN = re.compile(r"^\d{6}$", re.ASCII)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CninfoProfileProvider:
    descriptor = CNINFO_PROFILE_DESCRIPTOR

    def __init__(
        self,
        transport: CninfoProfileTransport,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if transport is None:
            raise ValueError("transport is required")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._transport = transport
        self._clock = clock

    @staticmethod
    def _validation_error(message: str, code: str) -> ProviderValidationError:
        return ProviderValidationError(
            provider_id=CNINFO_PROFILE_DESCRIPTOR.provider_id,
            operation="fetch_profile",
            safe_message=message,
            code=code,
        )

    @staticmethod
    def _transport_error(exc: Exception) -> ProviderError:
        common = {
            "provider_id": CNINFO_PROFILE_DESCRIPTOR.provider_id,
            "operation": "fetch_profile",
        }
        if isinstance(exc, TimeoutError):
            return ProviderTimeoutError(
                **common,
                safe_message="CNInfo profile request timed out",
                code="transport_timeout",
            )
        if isinstance(exc, CninfoTransportRateLimitError):
            return ProviderRateLimitError(
                **common,
                safe_message="CNInfo profile rate limit was reached",
                code="transport_rate_limit",
            )
        if isinstance(exc, CninfoTransportBlockedError):
            return ProviderBlockedError(
                **common,
                safe_message="CNInfo profile request was blocked",
                code="transport_blocked",
            )
        if isinstance(exc, (ConnectionError, OSError)):
            return ProviderNetworkError(
                **common,
                safe_message="CNInfo profile network request failed",
                code="transport_network",
            )
        return ProviderUnavailableError(
            **common,
            safe_message="CNInfo profile transport was unavailable",
            code="transport_unavailable",
        )

    def fetch_profile(
        self,
        security: Security,
    ) -> ProviderResult[SecurityProfile]:
        if not isinstance(security, Security):
            raise self._validation_error(
                "Security input is invalid",
                "invalid_security",
            )
        if security.market != "cn":
            raise self._validation_error(
                "CNInfo profile provider only supports the cn market",
                "unsupported_market",
            )
        if _CN_SYMBOL_PATTERN.fullmatch(security.symbol) is None:
            raise self._validation_error(
                "CN security symbol must contain exactly six ASCII digits",
                "invalid_symbol",
            )

        fetched_at = self._clock()
        if not is_timezone_aware(fetched_at):
            raise self._validation_error(
                "clock must return a timezone-aware datetime",
                "invalid_clock",
            )

        try:
            rows = self._transport.fetch_profile_rows(security.symbol)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._transport_error(exc) from exc

        return parse_cninfo_profile_rows(
            rows,
            security=security,
            fetched_at=fetched_at,
        )
