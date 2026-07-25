"""Eastmoney CN Profile 与公司新闻的纯离线 Provider。"""

from .constants import EASTMONEY_NEWS_DESCRIPTOR, EASTMONEY_PROFILE_DESCRIPTOR
from .news import EastmoneyNewsProvider
from .news_parser import (
    compute_eastmoney_news_content_hash,
    normalize_eastmoney_news_text,
    parse_eastmoney_news_rows,
)
from .news_transport import EastmoneyNewsTransport, NewsRow, NewsRows
from .parser import parse_eastmoney_profile_rows
from .profile import EastmoneyProfileProvider
from .transport import (
    EastmoneyProfileTransport,
    EastmoneyTransportBlockedError,
    EastmoneyTransportRateLimitError,
    ProfileRow,
    ProfileRows,
)

__all__ = [
    "EASTMONEY_NEWS_DESCRIPTOR",
    "EASTMONEY_PROFILE_DESCRIPTOR",
    "EastmoneyNewsProvider",
    "EastmoneyNewsTransport",
    "EastmoneyProfileProvider",
    "EastmoneyProfileTransport",
    "EastmoneyTransportBlockedError",
    "EastmoneyTransportRateLimitError",
    "NewsRow",
    "NewsRows",
    "ProfileRow",
    "ProfileRows",
    "compute_eastmoney_news_content_hash",
    "normalize_eastmoney_news_text",
    "parse_eastmoney_news_rows",
    "parse_eastmoney_profile_rows",
]
