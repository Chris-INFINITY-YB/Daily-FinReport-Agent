"""Provider 契约与旧数据源旁路；当前生产数据流尚未接入。"""

from .base import NewsProvider, ProfileProvider, QuoteProvider
from .contracts import ProviderCapability, ProviderDescriptor, ProviderResult
from .errors import (
    ProviderAuthenticationError,
    ProviderBlockedError,
    ProviderError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    provider_error_to_issue,
)
from .eastmoney import EASTMONEY_PROFILE_DESCRIPTOR, EastmoneyProfileProvider
from .legacy import LegacyDataSourceFacade, legacy_descriptor
from .tencent import TENCENT_QUOTE_DESCRIPTOR, TencentQuoteProvider

__all__ = [
    "EASTMONEY_PROFILE_DESCRIPTOR",
    "EastmoneyProfileProvider",
    "LegacyDataSourceFacade",
    "NewsProvider",
    "ProfileProvider",
    "ProviderAuthenticationError",
    "ProviderBlockedError",
    "ProviderCapability",
    "ProviderDescriptor",
    "ProviderError",
    "ProviderNetworkError",
    "ProviderParseError",
    "ProviderRateLimitError",
    "ProviderResult",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderValidationError",
    "QuoteProvider",
    "TENCENT_QUOTE_DESCRIPTOR",
    "TencentQuoteProvider",
    "legacy_descriptor",
    "provider_error_to_issue",
]
