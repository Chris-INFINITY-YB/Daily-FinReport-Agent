"""腾讯财经 A 股单点行情 Provider；仅支持注入式离线 Transport。"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timezone

from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.news import is_timezone_aware
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
    provider_error_to_issue,
)

from .constants import TENCENT_QUOTE_DESCRIPTOR
from .parser import parse_tencent_quote_response
from .symbols import build_tencent_symbol_list
from .transport import (
    TencentQuoteTransport,
    TencentTransportBlockedError,
    TencentTransportRateLimitError,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TencentQuoteProvider:
    descriptor = TENCENT_QUOTE_DESCRIPTOR

    def __init__(
        self,
        transport: TencentQuoteTransport,
        *,
        timeout_seconds: float = 10.0,
        batch_size: int = 50,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if transport is None:
            raise ValueError("transport is required")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or batch_size <= 0
        ):
            raise ValueError("batch_size must be a positive integer")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._transport = transport
        self._timeout_seconds = float(timeout_seconds)
        self._batch_size = batch_size
        self._clock = clock

    @staticmethod
    def _transport_error(exc: Exception) -> ProviderError:
        common = {
            "provider_id": TENCENT_QUOTE_DESCRIPTOR.provider_id,
            "operation": "fetch_quotes",
        }
        if isinstance(exc, TimeoutError):
            return ProviderTimeoutError(
                **common,
                safe_message="Tencent quote request timed out",
                code="transport_timeout",
            )
        if isinstance(exc, TencentTransportRateLimitError):
            return ProviderRateLimitError(
                **common,
                safe_message="Tencent quote rate limit was reached",
                code="transport_rate_limit",
            )
        if isinstance(exc, TencentTransportBlockedError):
            return ProviderBlockedError(
                **common,
                safe_message="Tencent quote request was blocked",
                code="transport_blocked",
            )
        if isinstance(exc, (ConnectionError, OSError)):
            return ProviderNetworkError(
                **common,
                safe_message="Tencent quote network request failed",
                code="transport_network",
            )
        return ProviderUnavailableError(
            **common,
            safe_message="Tencent quote transport was unavailable",
            code="transport_unavailable",
        )

    def fetch_quotes(
        self,
        securities: tuple[Security, ...],
    ) -> ProviderResult[MarketSnapshot]:
        if not isinstance(securities, tuple):
            raise ProviderValidationError(
                provider_id=self.descriptor.provider_id,
                operation="fetch_quotes",
                safe_message="securities must be a tuple",
                code="invalid_collection",
            )
        if not securities:
            return ProviderResult(provider=self.descriptor)

        symbols = build_tencent_symbol_list(securities)
        fetched_at = self._clock()
        if not is_timezone_aware(fetched_at):
            raise ProviderValidationError(
                provider_id=self.descriptor.provider_id,
                operation="fetch_quotes",
                safe_message="clock must return a timezone-aware datetime",
                code="invalid_clock",
            )

        items: list[MarketSnapshot] = []
        issues = []
        errors: list[ProviderError] = []
        successful_batches = 0
        for start in range(0, len(securities), self._batch_size):
            batch_securities = securities[start : start + self._batch_size]
            batch_symbols = symbols[start : start + self._batch_size]
            try:
                text = self._transport.fetch_quote_text(
                    batch_symbols,
                    timeout_seconds=self._timeout_seconds,
                )
                result = parse_tencent_quote_response(
                    text,
                    requested=batch_securities,
                    fetched_at=fetched_at,
                )
            except ProviderError as exc:
                errors.append(exc)
                continue
            except Exception as exc:
                error = self._transport_error(exc)
                error.__cause__ = exc
                errors.append(error)
                continue
            successful_batches += 1
            items.extend(result.items)
            issues.extend(result.issues)

        if successful_batches == 0 and errors:
            raise errors[0]
        for error in errors:
            issues.append(provider_error_to_issue(error, occurred_at=fetched_at))
        return ProviderResult(
            provider=self.descriptor,
            items=tuple(items),
            issues=tuple(issues),
        )
