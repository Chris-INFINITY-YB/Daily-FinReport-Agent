"""Provider 身份、能力与返回值契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from daily_report_agent.models.issues import DataIssue


_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_MARKETS = frozenset({"cn", "us"})


def normalize_provider_id(value: str) -> str:
    """校验并返回可持久化的稳定 Provider ID。"""
    if not isinstance(value, str):
        raise TypeError("provider_id 必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise ValueError("provider_id 不能为空")
    if _PROVIDER_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "provider_id 只能包含小写 ASCII 字母、数字、短横线和下划线"
        )
    return normalized


def normalize_provider_operation(value: str) -> str:
    """校验并返回适合指标、日志和持久化的 Provider 操作标识。"""
    if not isinstance(value, str):
        raise TypeError("operation 必须是字符串")
    normalized = value.strip()
    if _OPERATION_PATTERN.fullmatch(normalized) is None:
        raise ValueError("operation 必须是长度不超过 64 的安全小写短标识")
    return normalized


def normalize_provider_error_code(value: str | None) -> str | None:
    """校验不会携带自由文本或请求数据的 Provider 错误码。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("error_code 必须是字符串或 None")
    normalized = value.strip()
    if _ERROR_CODE_PATTERN.fullmatch(normalized) is None:
        raise ValueError("error_code 必须是长度不超过 64 的安全小写短标识或 None")
    return normalized


class ProviderCapability(str, Enum):
    QUOTE = "quote"
    NEWS = "news"
    PROFILE = "profile"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    display_name: str
    capabilities: frozenset[ProviderCapability]
    markets: frozenset[str]
    version: str | None = None
    supported_news_types: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        provider_id = normalize_provider_id(self.provider_id)
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("display_name 不能为空")
        if not isinstance(self.capabilities, frozenset):
            raise TypeError("capabilities 必须是 frozenset")
        if not self.capabilities:
            raise ValueError("capabilities 不能为空")
        if any(not isinstance(item, ProviderCapability) for item in self.capabilities):
            raise TypeError("capabilities 只能包含 ProviderCapability")
        if not isinstance(self.markets, frozenset):
            raise TypeError("markets 必须是 frozenset")
        if not self.markets:
            raise ValueError("markets 不能为空")
        normalized_markets = frozenset(
            item.strip() if isinstance(item, str) else item for item in self.markets
        )
        if not normalized_markets <= _ALLOWED_MARKETS:
            raise ValueError("markets 只允许 'cn' 或 'us'")
        if not isinstance(self.supported_news_types, frozenset):
            raise TypeError("supported_news_types 必须是 frozenset")
        normalized_news_types = frozenset(
            item.strip() if isinstance(item, str) else item
            for item in self.supported_news_types
        )
        if any(not isinstance(item, str) or not item for item in normalized_news_types):
            raise ValueError("supported_news_types 不能包含空值或非字符串")
        if normalized_news_types and ProviderCapability.NEWS not in self.capabilities:
            raise ValueError("仅 NEWS Provider 可声明 supported_news_types")
        if self.version is not None and (
            not isinstance(self.version, str) or not self.version.strip()
        ):
            raise ValueError("version 必须是非空字符串或 None")

        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "display_name", self.display_name.strip())
        object.__setattr__(self, "markets", normalized_markets)
        object.__setattr__(self, "supported_news_types", normalized_news_types)
        if self.version is not None:
            object.__setattr__(self, "version", self.version.strip())


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProviderResult(Generic[T]):
    """一次成功完成的 Provider 请求；请求失败应抛出 ProviderError。"""

    provider: ProviderDescriptor
    items: tuple[T, ...] = ()
    issues: tuple[DataIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider, ProviderDescriptor):
            raise TypeError("provider 必须是 ProviderDescriptor")
        if not isinstance(self.items, tuple):
            raise TypeError("items 必须是 tuple")
        if not isinstance(self.issues, tuple):
            raise TypeError("issues 必须是 tuple")
        if any(not isinstance(issue, DataIssue) for issue in self.issues):
            raise TypeError("issues 只能包含 DataIssue")
