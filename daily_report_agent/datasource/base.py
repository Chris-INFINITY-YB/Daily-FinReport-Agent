"""
统一数据源接口。美股、A股各实现一个子类, 上层通过 get_source(market) 获取。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class NewsItem:
    date: str        # YYYY-MM-DD
    headline: str
    summary: str = ""


@dataclass
class StockData:
    """一只股票抓取到的全部原始数据, 交给 analyzer 组装 prompt。"""
    symbol: str
    name: str = ""
    market: str = ""          # us | cn
    intro: str = ""           # 公司/标的简介
    start_date: str = ""
    end_date: str = ""
    start_price: float = 0.0
    end_price: float = 0.0
    pct_change: float = 0.0   # 区间涨跌幅(%)
    news: list = field(default_factory=list)   # list[NewsItem]
    error: str = ""           # 抓取失败时记录原因, 不为空则跳过分析


class DataSource(ABC):
    """数据源抽象接口。"""

    @abstractmethod
    def fetch(self, symbol: str, name: str, news_days: int, max_news: int) -> StockData:
        """抓取一只股票的行情+新闻, 返回 StockData。内部需自行兜底异常并写入 error 字段。"""
        raise NotImplementedError


def get_source(market: str) -> DataSource:
    """按市场返回对应数据源实例。"""
    market = (market or "").lower()
    if market == "us":
        from .us import USDataSource
        return USDataSource()
    if market == "cn":
        from .cn import CNDataSource
        return CNDataSource()
    raise ValueError(f"不支持的市场: {market!r} (仅支持 us / cn)")
