"""腾讯财经 A 股行情的离线 Provider 结构。"""

from .constants import TENCENT_QUOTE_DESCRIPTOR
from .parser import parse_tencent_quote_response
from .quote import TencentQuoteProvider
from .symbols import build_tencent_symbol_list, to_tencent_symbol
from .transport import (
    TencentQuoteTransport,
    TencentTransportBlockedError,
    TencentTransportRateLimitError,
)

__all__ = [
    "TENCENT_QUOTE_DESCRIPTOR",
    "TencentQuoteProvider",
    "TencentQuoteTransport",
    "TencentTransportBlockedError",
    "TencentTransportRateLimitError",
    "build_tencent_symbol_list",
    "parse_tencent_quote_response",
    "to_tencent_symbol",
]
