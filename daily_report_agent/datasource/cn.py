"""
A股数据源: akshare(免费免token)。
- 行情: ak.stock_zh_a_hist
- 个股新闻: ak.stock_news_em
- 简介: ak.stock_individual_info_em
akshare 接口偶有变动/限流, 每步都做兜底, 单步失败不影响其余字段。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .base import DataSource, StockData, NewsItem


class CNDataSource(DataSource):
    def __init__(self):
        import akshare as ak  # 延迟导入
        self._ak = ak

    def fetch(self, symbol: str, name: str, news_days: int, max_news: int) -> StockData:
        data = StockData(symbol=symbol, name=name, market="cn")
        end = datetime.now()
        start = end - timedelta(days=news_days)
        data.start_date = start.strftime("%Y-%m-%d")
        data.end_date = end.strftime("%Y-%m-%d")

        self._fetch_prices(symbol, data, start, end)
        self._fetch_intro(symbol, data)
        self._fetch_news(symbol, data, max_news)
        if not (data.news or data.end_price) and not data.error:
            data.error = "A股抓取无有效数据(可能限流或代码错误)"
        return data

    def _fetch_prices(self, symbol, data, start, end):
        try:
            df = self._ak.stock_zh_a_hist(
                symbol=symbol, period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                data.start_price = float(df["收盘"].iloc[0])
                data.end_price = float(df["收盘"].iloc[-1])
                if data.start_price:
                    data.pct_change = (data.end_price - data.start_price) / data.start_price * 100
        except Exception as e:
            data.error = f"A股行情失败: {e}"

    def _fetch_intro(self, symbol, data):
        try:
            info = self._ak.stock_individual_info_em(symbol=symbol)
            kv = dict(zip(info["item"], info["value"]))
            if not data.name:
                data.name = str(kv.get("股票简称", symbol))
            data.intro = (
                f"{data.name}({symbol}), 所属行业 {kv.get('行业', '未知')}, "
                f"总市值约 {kv.get('总市值', '未知')}。"
            )
        except Exception:
            if not data.name:
                data.name = symbol

    def _fetch_news(self, symbol, data, max_news):
        try:
            df = self._ak.stock_news_em(symbol=symbol)
            if df is None or df.empty:
                return
            df = df.head(max_news)
            for _, row in df.iterrows():
                pub = str(row.get("发布时间", ""))[:10]
                data.news.append(NewsItem(
                    date=pub,
                    headline=str(row.get("新闻标题", "")),
                    summary=str(row.get("新闻内容", ""))[:300],  # 内容较长, 截断控成本
                ))
        except Exception as e:
            if not data.error:
                data.error = f"A股新闻失败: {e}"
