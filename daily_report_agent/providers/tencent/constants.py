"""腾讯财经 Provider 的稳定身份。"""

from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderDescriptor,
)


TENCENT_QUOTE_DESCRIPTOR = ProviderDescriptor(
    provider_id="tencent-finance",
    display_name="Tencent Finance",
    capabilities=frozenset({ProviderCapability.QUOTE}),
    markets=frozenset({"cn"}),
    version=None,
)
