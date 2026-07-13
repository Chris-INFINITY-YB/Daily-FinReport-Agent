"""
美股数据源: finnhub(新闻) + yfinance(行情)。
逻辑沿用项目 fingpt/FinGPT_Forecaster/data.py 的 get_news/get_returns 思路,
但简化为"抓最近 N 天"而非按周分箱, 因为本项目只做日报不做训练。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from .base import DataSource, StockData, NewsItem


class USDataSource(DataSource):
    def __init__(self):
        # 延迟导入, 避免未装依赖时整个包无法 import
        import finnhub
        self._finnhub = finnhub.Client(api_key=os.environ.get("FINNHUB_KEY", ""))

    def fetch(self, symbol: str, name: str, news_days: int, max_news: int) -> StockData:
        data = StockData(symbol=symbol, name=name, market="us")
        end = datetime.now()
        start = end - timedelta(days=news_days)
        data.start_date = start.strftime("%Y-%m-%d")
        data.end_date = end.strftime("%Y-%m-%d")

        try:
            self._fetch_prices(symbol, data, start, end)
            self._fetch_intro(symbol, data)
            self._fetch_news(symbol, data, max_news)
        except Exception as e:  # 单只失败不影响整体
            data.error = f"美股抓取失败: {e}"
        return data

    def _fetch_prices(self, symbol, data, start, end):
        import yfinance as yf
        hist = yf.Ticker(symbol).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        if hist is None or hist.empty:
            return
        data.start_price = float(hist["Close"].iloc[0])
        data.end_price = float(hist["Close"].iloc[-1])
        if data.start_price:
            data.pct_change = (data.end_price - data.start_price) / data.start_price * 100

    def _fetch_intro(self, symbol, data):
        try:
            profile = self._finnhub.company_profile2(symbol=symbol)
        except Exception:
            profile = {}
        if profile:
            if not data.name:
                data.name = profile.get("name", symbol)
            data.intro = (
                f"{profile.get('name', symbol)} 属于 {profile.get('finnhubIndustry', '未知')} 行业, "
                f"在 {profile.get('exchange', '')} 上市, 股票代码 {symbol}, "
                f"市值约 {profile.get('marketCapitalization', 0):.0f} {profile.get('currency', 'USD')}。"
            )

    def _fetch_news(self, symbol, data, max_news):
        raw = self._finnhub.company_news(
            symbol, _from=data.start_date, to=data.end_date
        ) or []
        raw.sort(key=lambda n: n.get("datetime", 0), reverse=True)
        for n in raw[:max_news]:
            summary = n.get("summary", "") or ""
            # 过滤 Forecaster 里也过滤的营销垃圾
            if summary.startswith("Looking for stock market analysis"):
                continue
            data.news.append(NewsItem(
                date=datetime.fromtimestamp(n.get("datetime", 0)).strftime("%Y-%m-%d"),
                headline=n.get("headline", ""),
                summary=summary,
            ))
