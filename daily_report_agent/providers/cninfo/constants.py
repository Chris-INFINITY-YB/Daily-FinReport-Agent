"""CNInfo Provider 的稳定身份与能力声明。"""

from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderDescriptor,
)


CNINFO_PROFILE_DESCRIPTOR = ProviderDescriptor(
    provider_id="cninfo",
    display_name="CNInfo",
    capabilities=frozenset({ProviderCapability.PROFILE}),
    markets=frozenset({"cn"}),
    version=None,
)
