"""Eastmoney Profile 的同步只读 Transport 边界；没有在线实现。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias


ProfileRow: TypeAlias = Mapping[str, object]
ProfileRows: TypeAlias = tuple[ProfileRow, ...]


class EastmoneyProfileTransport(Protocol):
    def fetch_profile_rows(self, symbol: str) -> ProfileRows:
        """按标准化证券代码返回不可变的只读行记录集合。"""
        ...


class EastmoneyTransportBlockedError(Exception):
    """Transport 明确识别到请求被上游阻止。"""


class EastmoneyTransportRateLimitError(Exception):
    """Transport 明确识别到上游限流。"""
