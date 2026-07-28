"""CNInfo Profile 的同步只读 Transport 边界；没有在线实现。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias


CninfoProfileRow: TypeAlias = Mapping[str, object]
CninfoProfileRows: TypeAlias = tuple[CninfoProfileRow, ...]


class CninfoProfileTransport(Protocol):
    def fetch_profile_rows(self, symbol: str) -> CninfoProfileRows:
        """按规范化六位 ASCII 证券代码返回不可变的零行或一行宽表。"""
        ...


class CninfoTransportBlockedError(Exception):
    """Transport 明确识别到请求被上游阻止。"""


class CninfoTransportRateLimitError(Exception):
    """Transport 明确识别到上游限流。"""
