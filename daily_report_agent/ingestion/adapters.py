"""旧 StockData 到标准模型的单向旁路适配器。"""

from __future__ import annotations

from datetime import datetime, timezone

from daily_report_agent.datasource.base import StockData
from daily_report_agent.models.analysis import AnalysisInput
from daily_report_agent.models.collection import CollectedSecurityData
from daily_report_agent.models.issues import DataIssue, IssueCategory, IssueSeverity
from daily_report_agent.models.market import PriceWindow
from daily_report_agent.models.security import Security

from .normalizer import (
    has_explicit_time,
    normalize_datetime,
    normalize_news,
    normalize_symbol,
)


def _issue(
    *,
    category: IssueCategory,
    operation: str,
    message: str,
    occurred_at: datetime,
    details: dict[str, object] | None = None,
) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.WARNING,
        category=category,
        provider="legacy",
        operation=operation,
        message=message,
        retryable=False,
        occurred_at=occurred_at,
        details=details,
    )


def _has_price_window(data: StockData) -> bool:
    """至少一个区间价格明确非空且非旧默认零值时，才建立窗口。"""
    return any(
        value is not None and value != 0
        for value in (data.start_price, data.end_price)
    )


def stockdata_to_collection(
    data: StockData,
    *,
    fetched_at: datetime | None = None,
) -> CollectedSecurityData:
    """将旧 StockData 转为标准集合，不修改输入，也不参与生产数据流。"""
    normalized_fetched_at = normalize_datetime(
        fetched_at or datetime.now(timezone.utc)
    )
    symbol = normalize_symbol(data.symbol)
    name = str(data.name or "").strip()
    issues: list[DataIssue] = []

    if not name:
        name = symbol
        issues.append(
            _issue(
                category=IssueCategory.MISSING_DATA,
                operation="normalize_security",
                message="旧数据缺少证券名称，暂以 symbol 作为名称",
                occurred_at=normalized_fetched_at,
            )
        )

    security = Security(
        market=str(data.market or "").strip(),
        symbol=symbol,
        name=name,
    )

    legacy_error = str(data.error or "").strip()
    if legacy_error:
        issues.append(
            _issue(
                category=IssueCategory.UNKNOWN,
                operation="fetch",
                message=legacy_error,
                occurred_at=normalized_fetched_at,
            )
        )

    price_window = None
    if _has_price_window(data):
        price_window = PriceWindow(
            start_price=data.start_price,
            end_price=data.end_price,
            period_pct_change=data.pct_change,
        )
        issues.append(
            _issue(
                category=IssueCategory.MISSING_DATA,
                operation="normalize_market_time",
                message="旧区间行情没有精确到时刻的观察时间",
                occurred_at=normalized_fetched_at,
                details={
                    "start_date": data.start_date,
                    "end_date": data.end_date,
                },
            )
        )
    else:
        issues.append(
            _issue(
                category=IssueCategory.MISSING_DATA,
                operation="normalize_market",
                message="旧数据未提供可确认的区间行情",
                occurred_at=normalized_fetched_at,
            )
        )

    normalized_news = []
    for legacy_news in data.news:
        original_date = legacy_news.date
        try:
            published_at = normalize_datetime(original_date)
        except ValueError:
            published_at = normalized_fetched_at
            issues.append(
                _issue(
                    category=IssueCategory.PARSE,
                    operation="normalize_news_time",
                    message="旧新闻时间无法解析，暂使用 fetched_at",
                    occurred_at=normalized_fetched_at,
                    details={"original_date": original_date},
                )
            )
        else:
            if not has_explicit_time(original_date):
                issues.append(
                    _issue(
                        category=IssueCategory.MISSING_DATA,
                        operation="normalize_news_time",
                        message="旧新闻时间只有日期，缺少时分精度",
                        occurred_at=normalized_fetched_at,
                        details={"original_date": original_date},
                    )
                )

        try:
            item = normalize_news(
                headline=legacy_news.headline,
                summary=legacy_news.summary,
                published_at=published_at,
                fetched_at=normalized_fetched_at,
                symbol=symbol,
            )
        except ValueError as exc:
            issues.append(
                _issue(
                    category=IssueCategory.VALIDATION,
                    operation="normalize_news",
                    message=f"旧新闻未通过标准模型校验: {exc}",
                    occurred_at=normalized_fetched_at,
                )
            )
            continue
        normalized_news.append(item)

    return CollectedSecurityData(
        security=security,
        profile_text=str(data.intro or "").strip(),
        news=tuple(normalized_news),
        market_snapshots=(),
        price_window=price_window,
        issues=tuple(issues),
        raw_response_ids=(),
    )


def collection_to_analysis_input(
    collection: CollectedSecurityData,
) -> AnalysisInput:
    """无损复制标准采集结果中分析层需要的字段。"""
    return AnalysisInput(
        security=collection.security,
        profile_text=collection.profile_text,
        news=collection.news,
        price_window=collection.price_window,
        market_snapshots=collection.market_snapshots,
        issues=collection.issues,
    )


def stockdata_to_analysis_input(
    data: StockData,
    *,
    fetched_at: datetime | None = None,
) -> AnalysisInput:
    """复用既有适配链，将旧 StockData 转为标准分析输入。"""
    return collection_to_analysis_input(
        stockdata_to_collection(data, fetched_at=fetched_at)
    )
