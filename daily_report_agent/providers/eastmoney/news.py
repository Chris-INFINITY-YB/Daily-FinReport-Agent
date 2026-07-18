"""Eastmoney CN 公司新闻 Provider；仅支持显式注入的 Transport。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone

from daily_report_agent.models.issues import DataIssue, IssueCategory, IssueSeverity
from daily_report_agent.models.news import NewsItem, is_timezone_aware
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

from .constants import EASTMONEY_NEWS_DESCRIPTOR
from .news_parser import parse_eastmoney_news_rows
from .news_transport import EastmoneyNewsTransport
from .transport import (
    EastmoneyTransportBlockedError,
    EastmoneyTransportRateLimitError,
)


_OPERATION = "fetch_news"
_CN_SYMBOL_PATTERN = re.compile(r"^\d{6}$", re.ASCII)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EastmoneyNewsProvider:
    descriptor = EASTMONEY_NEWS_DESCRIPTOR

    def __init__(
        self,
        transport: EastmoneyNewsTransport,
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
            provider_id=EASTMONEY_NEWS_DESCRIPTOR.provider_id,
            operation=_OPERATION,
            safe_message=message,
            code=code,
        )

    @staticmethod
    def _transport_error(exc: Exception) -> ProviderError:
        common = {
            "provider_id": EASTMONEY_NEWS_DESCRIPTOR.provider_id,
            "operation": _OPERATION,
        }
        if isinstance(exc, TimeoutError):
            return ProviderTimeoutError(
                **common,
                safe_message="Eastmoney news request timed out",
                code="transport_timeout",
            )
        if isinstance(exc, EastmoneyTransportRateLimitError):
            return ProviderRateLimitError(
                **common,
                safe_message="Eastmoney news rate limit was reached",
                code="transport_rate_limit",
            )
        if isinstance(exc, EastmoneyTransportBlockedError):
            return ProviderBlockedError(
                **common,
                safe_message="Eastmoney news request was blocked",
                code="transport_blocked",
            )
        if isinstance(exc, (ConnectionError, OSError)):
            return ProviderNetworkError(
                **common,
                safe_message="Eastmoney news network request failed",
                code="transport_network",
            )
        return ProviderUnavailableError(
            **common,
            safe_message="Eastmoney news transport was unavailable",
            code="transport_unavailable",
        )

    @staticmethod
    def _empty_issue(fetched_at: datetime) -> DataIssue:
        return DataIssue(
            severity=IssueSeverity.INFO,
            category=IssueCategory.MISSING_DATA,
            provider=EASTMONEY_NEWS_DESCRIPTOR.provider_id,
            operation=_OPERATION,
            message="Eastmoney company news was unavailable in the requested window",
            retryable=False,
            occurred_at=fetched_at,
            code="news_not_found",
        )

    def fetch_news(
        self,
        security: Security,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> ProviderResult[NewsItem]:
        if not isinstance(security, Security):
            raise self._validation_error("Security input is invalid", "invalid_security")
        if security.market != "cn":
            raise self._validation_error(
                "Eastmoney news provider only supports the cn market",
                "unsupported_market",
            )
        if _CN_SYMBOL_PATTERN.fullmatch(security.symbol) is None:
            raise self._validation_error(
                "CN security symbol must contain exactly six ASCII digits",
                "invalid_symbol",
            )
        if not is_timezone_aware(since):
            raise self._validation_error(
                "since must be a timezone-aware datetime",
                "invalid_since",
            )
        if not is_timezone_aware(until):
            raise self._validation_error(
                "until must be a timezone-aware datetime",
                "invalid_until",
            )
        if since > until:
            raise self._validation_error(
                "since must not be later than until",
                "invalid_time_window",
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise self._validation_error(
                "limit must be a positive integer",
                "invalid_limit",
            )

        fetched_at = self._clock()
        if not is_timezone_aware(fetched_at):
            raise self._validation_error(
                "clock must return a timezone-aware datetime",
                "invalid_clock",
            )

        try:
            rows = self._transport.fetch_news_rows(security.symbol)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._transport_error(exc) from exc

        parsed = parse_eastmoney_news_rows(
            rows,
            security=security,
            fetched_at=fetched_at,
        )
        in_window = tuple(
            item for item in parsed.items if since <= item.published_at <= until
        )
        ordered = tuple(
            sorted(in_window, key=lambda item: item.published_at, reverse=True)
        )
        selected = ordered[:limit]
        issues = parsed.issues
        if not selected and not any(
            issue.code == "news_not_found" for issue in issues
        ):
            issues = (*issues, self._empty_issue(fetched_at))
        return ProviderResult(
            provider=EASTMONEY_NEWS_DESCRIPTOR,
            items=selected,
            issues=issues,
        )
