"""Eastmoney 公司新闻的同步只读 Transport 边界；没有在线实现。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias


NewsRow: TypeAlias = Mapping[str, object]
NewsRows: TypeAlias = tuple[NewsRow, ...]


class EastmoneyNewsTransport(Protocol):
    def fetch_news_rows(self, symbol: str) -> NewsRows:
        """返回模拟 AkShare 最终公开字段的不可变新闻行。"""
        ...
