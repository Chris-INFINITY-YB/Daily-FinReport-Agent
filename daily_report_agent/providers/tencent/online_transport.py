"""显式启用的腾讯行情在线 Transport；导入和构造均不发起请求。"""

from __future__ import annotations

import codecs
import math
import re
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
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

from .constants import TENCENT_QUOTE_DESCRIPTOR


TENCENT_QUOTE_ENDPOINT = "https://qt.gtimg.cn/"
TENCENT_QUOTE_HOST = "qt.gtimg.cn"
DEFAULT_USER_AGENT = "daily-report-agent-tencent-smoke/0.1"
DEFAULT_MAX_RESPONSE_BYTES = 128 * 1024
MAX_ONLINE_SYMBOLS = 5
_FALLBACK_ENCODING = "gb18030"
_SUPPORTED_ENCODINGS = frozenset({"gb18030", "gb2312", "gbk", "utf-8"})
_SYMBOL_PATTERN = re.compile(r"^(?:sh|sz)\d{6}$")
_RECORD_PATTERN = re.compile(r'v_((?:sh|sz)\d{6})="([^"]*)"')


@dataclass(frozen=True, slots=True)
class OnlineResponseMetadata:
    http_status: int
    content_type: str | None
    encoding: str
    response_bytes: int
    record_symbols: tuple[str, ...]
    record_statuses: tuple[str, ...]
    field_counts: tuple[int, ...]


class _Response(Protocol):
    status: int
    headers: object

    def read(self, amount: int = -1) -> bytes:
        ...

    def geturl(self) -> str:
        ...

    def __enter__(self) -> _Response:
        ...

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        ...


class _Opener(Protocol):
    def open(self, request: Request, timeout: float) -> _Response:
        ...


class _DisallowedRedirectError(Exception):
    pass


def _is_allowed_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == TENCENT_QUOTE_HOST
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443}
    )


class _TencentRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request,
        file_pointer,
        code,
        message,
        headers,
        new_url,
    ):
        if not _is_allowed_url(new_url):
            raise _DisallowedRedirectError("redirect target is not allowed")
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


def _provider_validation(message: str, code: str) -> ProviderValidationError:
    return ProviderValidationError(
        provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
        operation="fetch_quotes",
        safe_message=message,
        code=code,
    )


def _read_content_type(headers: object) -> str | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter("Content-Type")
    return str(value).strip() if value else None


def _read_header_charset(headers: object) -> str | None:
    getter = getattr(headers, "get_content_charset", None)
    if callable(getter):
        value = getter()
        return str(value).strip() if value else None
    content_type = _read_content_type(headers)
    if not content_type:
        return None
    match = re.search(r"(?:^|;)\s*charset\s*=\s*['\"]?([^;'\"\s]+)", content_type, re.I)
    return match.group(1) if match else None


def _normalize_encoding(value: str | None) -> str:
    candidate = value or _FALLBACK_ENCODING
    try:
        normalized = codecs.lookup(candidate).name
    except LookupError as exc:
        raise ProviderParseError(
            provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
            operation="fetch_quotes",
            safe_message="Tencent quote response declared an unsupported encoding",
            code="unsupported_encoding",
        ) from exc
    if normalized not in _SUPPORTED_ENCODINGS:
        error = ValueError("encoding is outside the approved Tencent set")
        raise ProviderParseError(
            provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
            operation="fetch_quotes",
            safe_message="Tencent quote response declared an unsupported encoding",
            code="unsupported_encoding",
        ) from error
    return normalized


class TencentOnlineQuoteTransport:
    """固定腾讯域名、有限响应大小、无重试的同步在线 Transport。"""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: _Opener | None = None,
    ) -> None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must be a non-empty string")
        if "\r" in user_agent or "\n" in user_agent:
            raise ValueError("user_agent must not contain line breaks")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes <= 0
        ):
            raise ValueError("max_response_bytes must be a positive integer")
        self._user_agent = user_agent.strip()
        self._max_response_bytes = max_response_bytes
        self._opener = opener or build_opener(_TencentRedirectHandler())
        self._request_count = 0
        self._last_response_metadata: OnlineResponseMetadata | None = None

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def last_response_metadata(self) -> OnlineResponseMetadata | None:
        return self._last_response_metadata

    @staticmethod
    def _raise_http_error(exc: HTTPError) -> None:
        common = {
            "provider_id": TENCENT_QUOTE_DESCRIPTOR.provider_id,
            "operation": "fetch_quotes",
            "http_status": exc.code,
        }
        if exc.code == 429:
            raise ProviderRateLimitError(
                **common,
                safe_message="Tencent quote endpoint rate limited the request",
                code="http_429",
            ) from exc
        if exc.code == 403:
            raise ProviderBlockedError(
                **common,
                safe_message="Tencent quote endpoint blocked the request",
                code="http_403",
            ) from exc
        raise ProviderUnavailableError(
            **common,
            safe_message="Tencent quote endpoint returned an HTTP error",
            code="http_error",
        ) from exc

    @staticmethod
    def _validate_request(
        symbols: tuple[str, ...],
        timeout_seconds: float,
    ) -> None:
        if not isinstance(symbols, tuple):
            raise _provider_validation("symbols must be a tuple", "invalid_symbols")
        if not 1 <= len(symbols) <= MAX_ONLINE_SYMBOLS:
            raise _provider_validation(
                "Online Tencent requests require between one and five symbols",
                "invalid_symbol_count",
            )
        if len(symbols) != len(set(symbols)) or any(
            not isinstance(symbol, str) or _SYMBOL_PATTERN.fullmatch(symbol) is None
            for symbol in symbols
        ):
            raise _provider_validation(
                "Online Tencent symbols are invalid or duplicated",
                "invalid_symbols",
            )
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise _provider_validation(
                "timeout_seconds must be a positive finite number",
                "invalid_timeout",
            )

    def fetch_quote_text(
        self,
        symbols: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> str:
        self._validate_request(symbols, timeout_seconds)
        query = urlencode({"q": ",".join(symbols)}, safe=",")
        request = Request(
            f"{TENCENT_QUOTE_ENDPOINT}?{query}",
            headers={
                "Accept": "text/plain",
                "User-Agent": self._user_agent,
            },
            method="GET",
        )
        self._request_count += 1
        self._last_response_metadata = None
        try:
            with self._opener.open(request, timeout=float(timeout_seconds)) as response:
                final_url = response.geturl()
                if not _is_allowed_url(final_url):
                    raise _DisallowedRedirectError("response URL is not allowed")
                raw = response.read(self._max_response_bytes + 1)
                if len(raw) > self._max_response_bytes:
                    error = ValueError("response exceeded configured byte limit")
                    raise ProviderParseError(
                        provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                        operation="fetch_quotes",
                        safe_message="Tencent quote response exceeded the size limit",
                        code="response_too_large",
                    ) from error
                content_type = _read_content_type(response.headers)
                encoding = _normalize_encoding(_read_header_charset(response.headers))
                try:
                    text = raw.decode(encoding)
                except UnicodeDecodeError as exc:
                    raise ProviderParseError(
                        provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                        operation="fetch_quotes",
                        safe_message="Tencent quote response could not be decoded",
                        code="decode_failed",
                    ) from exc
                records = tuple(_RECORD_PATTERN.finditer(text))
                self._last_response_metadata = OnlineResponseMetadata(
                    http_status=int(response.status),
                    content_type=content_type,
                    encoding=encoding,
                    response_bytes=len(raw),
                    record_symbols=tuple(match.group(1) for match in records),
                    record_statuses=tuple(
                        match.group(2).split("~", 1)[0] if match.group(2) else ""
                        for match in records
                    ),
                    field_counts=tuple(
                        len(match.group(2).split("~")) if match.group(2) else 0
                        for match in records
                    ),
                )
                return text
        except HTTPError as exc:
            self._raise_http_error(exc)
        except _DisallowedRedirectError as exc:
            raise ProviderBlockedError(
                provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                operation="fetch_quotes",
                safe_message="Tencent quote response redirected outside the allowed host",
                code="unsafe_redirect",
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeoutError(
                    provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                    operation="fetch_quotes",
                    safe_message="Tencent quote request timed out",
                    code="network_timeout",
                ) from exc
            raise ProviderNetworkError(
                provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                operation="fetch_quotes",
                safe_message="Tencent quote network request failed",
                code="network_error",
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError(
                provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                operation="fetch_quotes",
                safe_message="Tencent quote request timed out",
                code="network_timeout",
            ) from exc
        except OSError as exc:
            raise ProviderNetworkError(
                provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
                operation="fetch_quotes",
                safe_message="Tencent quote network request failed",
                code="network_error",
            ) from exc

        raise AssertionError("unreachable")
