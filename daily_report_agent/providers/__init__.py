"""Provider 契约与旧数据源旁路；当前生产数据流尚未接入。"""

from .base import NewsProvider, ProfileProvider, QuoteProvider
from .contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
    normalize_provider_error_code,
    normalize_provider_id,
    normalize_provider_operation,
)
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
from .eastmoney import (
    EASTMONEY_NEWS_DESCRIPTOR,
    EASTMONEY_PROFILE_DESCRIPTOR,
    EastmoneyNewsProvider,
    EastmoneyProfileProvider,
)
from .legacy import LegacyDataSourceFacade, legacy_descriptor
from .tencent import TENCENT_QUOTE_DESCRIPTOR, TencentQuoteProvider
from .telemetry import (
    ProviderMetricEmitter,
    ProviderMetricEvent,
    ProviderMetricStatus,
    emit_provider_metric_safely,
    format_provider_metric_event,
    log_provider_metric,
)

__all__ = [
    "EASTMONEY_NEWS_DESCRIPTOR",
    "EASTMONEY_PROFILE_DESCRIPTOR",
    "EastmoneyNewsProvider",
    "EastmoneyProfileProvider",
    "LegacyDataSourceFacade",
    "NewsProvider",
    "ProfileProvider",
    "ProviderAuthenticationError",
    "ProviderBlockedError",
    "ProviderCapability",
    "ProviderDescriptor",
    "ProviderError",
    "ProviderMetricEmitter",
    "ProviderMetricEvent",
    "ProviderMetricStatus",
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
    "emit_provider_metric_safely",
    "format_provider_metric_event",
    "log_provider_metric",
    "normalize_provider_error_code",
    "normalize_provider_id",
    "normalize_provider_operation",
    "provider_error_to_issue",
]
