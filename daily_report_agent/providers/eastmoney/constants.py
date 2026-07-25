"""Eastmoney CN Provider 的稳定能力身份。"""

from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderDescriptor,
)


EASTMONEY_PROFILE_DESCRIPTOR = ProviderDescriptor(
    provider_id="eastmoney",
    display_name="Eastmoney",
    capabilities=frozenset({ProviderCapability.PROFILE}),
    markets=frozenset({"cn"}),
    version=None,
)


EASTMONEY_NEWS_DESCRIPTOR = ProviderDescriptor(
    provider_id="eastmoney",
    display_name="Eastmoney",
    capabilities=frozenset({ProviderCapability.NEWS}),
    markets=frozenset({"cn"}),
    version=None,
    supported_news_types=frozenset({"news"}),
)
