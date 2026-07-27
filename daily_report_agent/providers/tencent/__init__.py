"""腾讯财经 A 股行情的离线 Provider 结构。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .constants import TENCENT_QUOTE_DESCRIPTOR
from .parser import parse_tencent_quote_response
from .quote import TencentQuoteProvider
from .symbols import build_tencent_symbol_list, to_tencent_symbol
from .transport import (
    TencentQuoteTransport,
    TencentTransportBlockedError,
    TencentTransportRateLimitError,
)

if TYPE_CHECKING:
    from .online_transport import (
        OnlineResponseMetadata,
        TencentOnlineQuoteTransport,
    )


def __getattr__(name: str):
    """仅在显式请求在线类型时加载网络 Transport 模块。"""
    if name in {"OnlineResponseMetadata", "TencentOnlineQuoteTransport"}:
        from . import online_transport

        return getattr(online_transport, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TENCENT_QUOTE_DESCRIPTOR",
    "OnlineResponseMetadata",
    "TencentOnlineQuoteTransport",
    "TencentQuoteProvider",
    "TencentQuoteTransport",
    "TencentTransportBlockedError",
    "TencentTransportRateLimitError",
    "build_tencent_symbol_list",
    "parse_tencent_quote_response",
    "to_tencent_symbol",
]
