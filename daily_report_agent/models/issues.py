"""标准化数据质量问题。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .news import is_timezone_aware


class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCategory(str, Enum):
    NETWORK = "network"
    PARSE = "parse"
    AUTH = "auth"
    MISSING_DATA = "missing_data"
    RATE_LIMIT = "rate_limit"
    VALIDATION = "validation"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DataIssue:
    severity: IssueSeverity
    category: IssueCategory
    provider: str
    operation: str
    message: str
    retryable: bool
    occurred_at: datetime
    code: str | None = None
    http_status: int | None = None
    details: dict[str, object] | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider", "operation", "message"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 不能为空")
            object.__setattr__(self, field_name, value.strip())
        if not is_timezone_aware(self.occurred_at):
            raise ValueError("occurred_at 必须是 timezone-aware datetime")
