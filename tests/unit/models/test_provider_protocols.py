from typing import get_type_hints

import pytest

from daily_report_agent.models.profile import SecurityProfile
from daily_report_agent.models.security import Security
from daily_report_agent.providers.base import (
    NewsProvider,
    ProfileProvider,
    QuoteProvider,
)
from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
)
from daily_report_agent.providers.errors import (
    ProviderError,
    ProviderUnavailableError,
)


def test_provider_contracts_are_protocols_only() -> None:
    for provider_protocol in (QuoteProvider, NewsProvider, ProfileProvider):
        assert provider_protocol._is_protocol is True

    with pytest.raises(TypeError, match="Protocols cannot be instantiated"):
        ProfileProvider()


def test_profile_provider_uses_standard_input_and_result_annotations() -> None:
    annotations = get_type_hints(ProfileProvider.fetch_profile)

    assert annotations["security"] is Security
    assert annotations["return"] == ProviderResult[SecurityProfile]


def test_profile_empty_success_and_request_failure_are_distinct() -> None:
    descriptor = ProviderDescriptor(
        provider_id="profile-provider",
        display_name="Profile Provider",
        capabilities=frozenset({ProviderCapability.PROFILE}),
        markets=frozenset({"cn"}),
    )
    empty_success: ProviderResult[SecurityProfile] = ProviderResult(
        provider=descriptor
    )

    assert empty_success.items == ()

    failure = ProviderUnavailableError(
        provider_id=descriptor.provider_id,
        operation="fetch_profile",
        safe_message=(
            "request https://profile.test/data?token=secret failed; "
            "token=secret"
        ),
    )
    assert isinstance(failure, ProviderError)
    assert "https://" not in failure.safe_message
    assert "secret" not in failure.safe_message

    with pytest.raises(ProviderUnavailableError) as raised:
        raise failure
    assert raised.value is failure
