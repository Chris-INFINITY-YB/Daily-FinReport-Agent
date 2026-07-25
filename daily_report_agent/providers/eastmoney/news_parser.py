"""Eastmoney 公开新闻行到标准 NewsItem 的纯解析器。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.models.news import NewsItem, is_timezone_aware
from daily_report_agent.models.security import Security
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.errors import (
    ProviderParseError,
    ProviderValidationError,
)

from .constants import EASTMONEY_NEWS_DESCRIPTOR
from .news_transport import NewsRows


_OPERATION = "fetch_news"
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_NAIVE_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
_OFFSET_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$",
    re.ASCII,
)
_ABSOLUTE_HTTP_URL_RE = re.compile(
    r"^https?://[^\s/?#]+(?:[/?#][^\s]*)?$",
    re.IGNORECASE,
)


class _NewsParseError(ValueError):
    pass


def _validation_error(message: str, code: str) -> ProviderValidationError:
    return ProviderValidationError(
        provider_id=EASTMONEY_NEWS_DESCRIPTOR.provider_id,
        operation=_OPERATION,
        safe_message=message,
        code=code,
    )


def _parse_error(message: str, code: str, cause: Exception) -> None:
    raise ProviderParseError(
        provider_id=EASTMONEY_NEWS_DESCRIPTOR.provider_id,
        operation=_OPERATION,
        safe_message=message,
        code=code,
    ) from cause


def _issue(
    *,
    fetched_at: datetime,
    severity: IssueSeverity,
    category: IssueCategory,
    message: str,
    code: str,
) -> DataIssue:
    return DataIssue(
        severity=severity,
        category=category,
        provider=EASTMONEY_NEWS_DESCRIPTOR.provider_id,
        operation=_OPERATION,
        message=message,
        retryable=False,
        occurred_at=fetched_at,
        code=code,
    )


def normalize_eastmoney_news_text(value: str) -> str:
    """仅清理已确认高亮标记，再执行 NFKC 与确定性空白规范化。"""
    text = value.replace("(<em>", "").replace("</em>)", "")
    text = text.replace("<em>", "").replace("</em>", "")
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def compute_eastmoney_news_content_hash(
    title: str,
    summary: str,
    content: str | None = None,
) -> str:
    """按 news-content-v1 长度前缀协议计算确定性 SHA-256。"""
    payload = bytearray(b"news-content-v1\0")
    for value in (title, summary, content):
        if value is None:
            payload.extend(b"N")
            continue
        encoded = value.encode("utf-8")
        payload.extend(b"S")
        payload.extend(len(encoded).to_bytes(8, "big", signed=False))
        payload.extend(encoded)
    return hashlib.sha256(payload).hexdigest()


def _parse_published_at(value: object) -> tuple[datetime | None, bool]:
    if not isinstance(value, str):
        return None, False
    candidate = value.strip()
    if not candidate:
        return None, False

    if _DATE_ONLY_RE.fullmatch(candidate):
        try:
            parsed_date = datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError:
            return None, False
        return parsed_date.replace(tzinfo=_SHANGHAI_TZ), True

    for date_format in _NAIVE_DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(candidate, date_format)
        except ValueError:
            continue
        return parsed.replace(tzinfo=_SHANGHAI_TZ), False

    if _OFFSET_DATETIME_RE.fullmatch(candidate):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return None, False
        if is_timezone_aware(parsed):
            return parsed, False
    return None, False


def _parse_url(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, "missing_news_url"
    candidate = value.strip()
    if _ABSOLUTE_HTTP_URL_RE.fullmatch(candidate) is None:
        return None, "invalid_news_url"
    return candidate, None


def parse_eastmoney_news_rows(
    rows: NewsRows,
    *,
    security: Security,
    fetched_at: datetime,
) -> ProviderResult[NewsItem]:
    """解析公开候选字段；单条坏记录不会丢弃其他合法记录。"""
    if not isinstance(security, Security):
        raise _validation_error("Security input is invalid", "invalid_security")
    if security.market != "cn":
        raise _validation_error(
            "Eastmoney news provider only supports the cn market",
            "unsupported_market",
        )
    if not is_timezone_aware(fetched_at):
        raise _validation_error(
            "fetched_at must be a timezone-aware datetime",
            "invalid_fetched_at",
        )
    if not isinstance(rows, tuple):
        _parse_error(
            "Eastmoney news rows must be an immutable tuple",
            "invalid_news_response",
            _NewsParseError("rows are not a tuple"),
        )
    if not rows:
        return ProviderResult(
            provider=EASTMONEY_NEWS_DESCRIPTOR,
            issues=(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.INFO,
                    category=IssueCategory.MISSING_DATA,
                    message="Eastmoney company news was unavailable",
                    code="news_not_found",
                ),
            ),
        )

    items: list[NewsItem] = []
    issues: list[DataIssue] = []
    for row in rows:
        if not isinstance(row, Mapping):
            issues.append(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.PARSE,
                    message="Eastmoney news record structure was invalid",
                    code="invalid_news_record",
                )
            )
            continue

        raw_title = row.get("新闻标题")
        if not isinstance(raw_title, str):
            title = ""
        else:
            title = normalize_eastmoney_news_text(raw_title)
        if not title:
            issues.append(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.PARSE,
                    message="Eastmoney news title was unavailable",
                    code="missing_news_title",
                )
            )
            continue

        published_at, date_only = _parse_published_at(row.get("发布时间"))
        if published_at is None:
            issues.append(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.PARSE,
                    message="Eastmoney news published time was invalid",
                    code="invalid_published_at",
                )
            )
            continue
        if date_only:
            issues.append(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.PARSE,
                    message="Eastmoney news published time had date-only precision",
                    code="published_time_date_only",
                )
            )

        raw_summary = row.get("新闻内容")
        if isinstance(raw_summary, str):
            summary = normalize_eastmoney_news_text(raw_summary)
        else:
            summary = ""
        if not summary:
            issues.append(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.MISSING_DATA,
                    message="Eastmoney news summary was unavailable",
                    code="missing_news_summary",
                )
            )

        url, url_issue_code = _parse_url(row.get("新闻链接"))
        if url_issue_code is not None:
            issues.append(
                _issue(
                    fetched_at=fetched_at,
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.MISSING_DATA,
                    message="Eastmoney news URL was unavailable",
                    code=url_issue_code,
                )
            )

        content_hash = compute_eastmoney_news_content_hash(title, summary, None)
        items.append(
            NewsItem(
                id=f"eastmoney:{content_hash}",
                external_id=None,
                source=EASTMONEY_NEWS_DESCRIPTOR.provider_id,
                source_type="news",
                title=title,
                summary=summary,
                content=None,
                url=url,
                published_at=published_at,
                fetched_at=fetched_at,
                language="zh",
                content_hash=content_hash,
                related_symbols=(security.symbol,),
                source_reliability=None,
            )
        )

    return ProviderResult(
        provider=EASTMONEY_NEWS_DESCRIPTOR,
        items=tuple(items),
        issues=tuple(issues),
    )
