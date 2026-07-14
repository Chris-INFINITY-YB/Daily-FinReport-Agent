from __future__ import annotations

import importlib
import socket
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from daily_report_agent.providers.errors import (
    ProviderBlockedError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from daily_report_agent.providers.tencent import online_transport as online_module
from daily_report_agent.providers.tencent.online_transport import (
    TENCENT_QUOTE_ENDPOINT,
    TencentOnlineQuoteTransport,
)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str | None = "text/plain; charset=GBK",
        status: int = 200,
        final_url: str = TENCENT_QUOTE_ENDPOINT,
    ) -> None:
        self.body = body
        self.status = status
        self.final_url = final_url
        self.headers = Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]

    def geturl(self) -> str:
        return self.final_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class FakeOpener:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _transport(outcome: object, **kwargs) -> tuple[TencentOnlineQuoteTransport, FakeOpener]:
    opener = FakeOpener(outcome)
    return TencentOnlineQuoteTransport(opener=opener, **kwargs), opener


def test_normal_gbk_response_builds_fixed_host_request_and_metadata() -> None:
    text = 'v_sh600000="1~浦发银行~600000~10.25~10.00";'
    transport, opener = _transport(FakeResponse(text.encode("gbk")))

    result = transport.fetch_quote_text(
        ("sh600000", "sz000001"),
        timeout_seconds=2.5,
    )

    assert result == text
    assert transport.request_count == 1
    request, timeout = opener.calls[0]
    parsed = urlparse(request.full_url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "qt.gtimg.cn"
    assert parse_qs(parsed.query) == {"q": ["sh600000,sz000001"]}
    assert timeout == 2.5
    headers = {key.lower(): value for key, value in request.header_items()}
    assert "cookie" not in headers
    assert headers["accept"] == "text/plain"
    metadata = transport.last_response_metadata
    assert metadata is not None
    assert metadata.http_status == 200
    assert metadata.encoding == "gbk"
    assert metadata.record_symbols == ("sh600000",)
    assert metadata.record_statuses == ("1",)
    assert metadata.field_counts == (5,)


def test_missing_charset_uses_explicit_gb18030_fallback() -> None:
    text = 'v_sz000001="1~平安银行~000001~12.34~12.20";'
    transport, _ = _transport(
        FakeResponse(text.encode("gbk"), content_type="text/plain")
    )
    assert transport.fetch_quote_text(("sz000001",), timeout_seconds=1) == text
    assert transport.last_response_metadata is not None
    assert transport.last_response_metadata.encoding == "gb18030"


@pytest.mark.parametrize(
    ("content_type", "body", "code"),
    [
        ("text/plain; charset=iso-8859-1", b"plain", "unsupported_encoding"),
        ("text/plain; charset=utf-8", b"\xff\xff", "decode_failed"),
    ],
)
def test_unsupported_encoding_and_invalid_bytes_are_parse_errors(
    content_type: str,
    body: bytes,
    code: str,
) -> None:
    transport, _ = _transport(FakeResponse(body, content_type=content_type))
    with pytest.raises(ProviderParseError) as caught:
        transport.fetch_quote_text(("sh600000",), timeout_seconds=1)
    assert caught.value.code == code
    assert caught.value.__cause__ is not None
    assert body.hex() not in caught.value.safe_message


def test_response_size_limit_is_enforced() -> None:
    transport, _ = _transport(FakeResponse(b"x" * 17), max_response_bytes=16)
    with pytest.raises(ProviderParseError) as caught:
        transport.fetch_quote_text(("sh600000",), timeout_seconds=1)
    assert caught.value.code == "response_too_large"
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    ("status", "error_type", "code", "retryable"),
    [
        (403, ProviderBlockedError, "http_403", True),
        (429, ProviderRateLimitError, "http_429", True),
        (503, ProviderUnavailableError, "http_error", True),
    ],
)
def test_http_errors_are_safely_mapped(
    status: int,
    error_type: type[Exception],
    code: str,
    retryable: bool,
) -> None:
    error = HTTPError(
        "https://qt.gtimg.cn/?q=secret",
        status,
        "unsafe body text",
        Message(),
        None,
    )
    transport, _ = _transport(error)
    with pytest.raises(error_type) as caught:
        transport.fetch_quote_text(("sh600000",), timeout_seconds=1)
    assert caught.value.http_status == status
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert caught.value.__cause__ is error
    assert "secret" not in caught.value.safe_message


@pytest.mark.parametrize(
    ("outcome", "error_type", "code"),
    [
        (URLError(TimeoutError("Bearer secret")), ProviderTimeoutError, "network_timeout"),
        (URLError(OSError("https://secret.test?q=key")), ProviderNetworkError, "network_error"),
        (socket.timeout("token=secret"), ProviderTimeoutError, "network_timeout"),
    ],
)
def test_network_errors_are_safely_mapped(
    outcome: Exception,
    error_type: type[Exception],
    code: str,
) -> None:
    transport, _ = _transport(outcome)
    with pytest.raises(error_type) as caught:
        transport.fetch_quote_text(("sh600000",), timeout_seconds=1)
    assert caught.value.code == code
    assert caught.value.__cause__ is outcome
    assert "secret" not in caught.value.safe_message.lower()


def test_redirect_outside_tencent_is_blocked() -> None:
    transport, _ = _transport(
        FakeResponse(b"safe", final_url="https://evil.example/?q=sh600000")
    )
    with pytest.raises(ProviderBlockedError) as caught:
        transport.fetch_quote_text(("sh600000",), timeout_seconds=1)
    assert caught.value.code == "unsafe_redirect"
    assert caught.value.__cause__ is not None
    assert "evil.example" not in caught.value.safe_message


@pytest.mark.parametrize(
    ("symbols", "timeout"),
    [
        ((), 1),
        (("sh600000",) * 6, 1),
        (("xx600000",), 1),
        (("sh600000", "sh600000"), 1),
        (("sh600000",), 0),
    ],
)
def test_invalid_requests_are_rejected_before_open(
    symbols: tuple[str, ...],
    timeout: float,
) -> None:
    transport, opener = _transport(FakeResponse(b"unused"))
    with pytest.raises(ProviderValidationError):
        transport.fetch_quote_text(symbols, timeout_seconds=timeout)
    assert opener.calls == []
    assert transport.request_count == 0


def test_import_and_injected_construction_do_not_access_network(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("import and construction must stay offline")

    monkeypatch.setattr(socket, "socket", fail)
    reloaded = importlib.reload(online_module)
    opener = FakeOpener(FakeResponse(b"unused"))
    transport = reloaded.TencentOnlineQuoteTransport(opener=opener)
    assert transport.request_count == 0
    assert opener.calls == []
