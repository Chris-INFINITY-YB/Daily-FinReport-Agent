"""腾讯行情专用同步 Transport 边界；本阶段没有在线实现。"""

from __future__ import annotations

from typing import Protocol


class TencentQuoteTransport(Protocol):
    def fetch_quote_text(
        self,
        symbols: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> str:
        """返回腾讯行情原始文本。"""
        ...


class TencentTransportBlockedError(Exception):
    """Transport 明确识别到请求被上游阻止。"""


class TencentTransportRateLimitError(Exception):
    """Transport 明确识别到上游限流。"""
