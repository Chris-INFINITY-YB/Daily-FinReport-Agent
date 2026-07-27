"""Provider 的最小安全指标事件与标准日志输出。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .contracts import (
    normalize_provider_error_code,
    normalize_provider_id,
    normalize_provider_operation,
)


class ProviderMetricStatus(str, Enum):
    """Provider 逻辑调用允许记录的封闭终态集合。"""

    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


def _validate_count(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} 必须是整数且不能是 bool")
    if value < 0:
        raise ValueError(f"{name} 不得为负数")
    return value


@dataclass(frozen=True, slots=True)
class ProviderMetricEvent:
    """只允许携带聚合调用指标；字段集合本身即是安全白名单。"""

    provider_id: str
    operation: str
    status: ProviderMetricStatus
    duration_ms: int
    item_count: int = 0
    issue_count: int = 0
    retry_count: int = 0
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProviderMetricStatus):
            raise TypeError("status 必须是 ProviderMetricStatus")
        object.__setattr__(self, "provider_id", normalize_provider_id(self.provider_id))
        object.__setattr__(
            self,
            "operation",
            normalize_provider_operation(self.operation),
        )
        object.__setattr__(
            self,
            "duration_ms",
            _validate_count("duration_ms", self.duration_ms),
        )
        object.__setattr__(
            self,
            "item_count",
            _validate_count("item_count", self.item_count),
        )
        object.__setattr__(
            self,
            "issue_count",
            _validate_count("issue_count", self.issue_count),
        )
        object.__setattr__(
            self,
            "retry_count",
            _validate_count("retry_count", self.retry_count),
        )
        object.__setattr__(
            self,
            "error_code",
            normalize_provider_error_code(self.error_code),
        )


ProviderMetricEmitter = Callable[[ProviderMetricEvent], None]
_LOGGER = logging.getLogger("daily_report_agent.provider_metrics")


def format_provider_metric_event(event: ProviderMetricEvent) -> str:
    """按固定字段顺序生成不含自由文本的单行 JSON。"""
    if not isinstance(event, ProviderMetricEvent):
        raise TypeError("event 必须是 ProviderMetricEvent")
    payload = {
        "provider_id": event.provider_id,
        "operation": event.operation,
        "status": event.status.value,
        "duration_ms": event.duration_ms,
        "item_count": event.item_count,
        "issue_count": event.issue_count,
        "retry_count": event.retry_count,
        "error_code": event.error_code,
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def log_provider_metric(event: ProviderMetricEvent) -> None:
    """使用标准日志系统输出一个已验证事件，不创建日志文件。"""
    _LOGGER.info("%s", format_provider_metric_event(event))


def emit_provider_metric_safely(
    event: ProviderMetricEvent,
    emitter: ProviderMetricEmitter = log_provider_metric,
) -> None:
    """隔离普通输出失败，但不吞掉进程控制流异常。"""
    try:
        emitter(event)
    except Exception:
        return
