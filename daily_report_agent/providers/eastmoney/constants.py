"""Eastmoney CN Profile Provider 的稳定身份。"""

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
