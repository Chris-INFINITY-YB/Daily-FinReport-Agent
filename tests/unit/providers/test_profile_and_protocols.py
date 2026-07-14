from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daily_report_agent.models import (
    MarketSnapshot,
    NewsItem,
    Security,
    SecurityProfile,
)
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


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def descriptor(capability: ProviderCapability) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id="offline-fake",
        display_name="Offline Fake",
        capabilities=frozenset({capability}),
        markets=frozenset({"us"}),
    )


def profile(**overrides) -> SecurityProfile:
    values = {
        "symbol": " AAPL ",
        "market": "us",
        "name": "Apple",
        "exchange": "NASDAQ",
        "currency": "USD",
        "industry": "Technology",
        "description": None,
        "source": "offline-fake",
        "fetched_at": NOW,
    }
    values.update(overrides)
    return SecurityProfile(**values)


def test_security_profile_normalizes_identity_and_accepts_no_description() -> None:
    value = profile()
    assert value.symbol == "AAPL"
    assert value.description is None
    assert "market_cap" not in SecurityProfile.__dataclass_fields__


def test_security_profile_requires_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        profile(fetched_at=datetime(2026, 7, 14))


@pytest.mark.parametrize("market", ["hk", "", "china"])
def test_security_profile_validates_market(market: str) -> None:
    with pytest.raises(ValueError):
        profile(market=market)


@pytest.mark.parametrize("source", ["", "Fake Provider", "https://fake.test", "FAKE"])
def test_security_profile_validates_stable_source(source: str) -> None:
    with pytest.raises(ValueError):
        profile(source=source)


class FakeQuoteProvider:
    descriptor = descriptor(ProviderCapability.QUOTE)

    def fetch_quotes(
        self, securities: tuple[Security, ...]
    ) -> ProviderResult[MarketSnapshot]:
        return ProviderResult(
            provider=self.descriptor,
            items=(
                MarketSnapshot(
                    symbol=securities[0].symbol,
                    observed_at=NOW,
                    source=self.descriptor.provider_id,
                    price=210.0,
                ),
            ),
        )


class FakeNewsProvider:
    descriptor = descriptor(ProviderCapability.NEWS)

    def fetch_news(
        self,
        security: Security,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> ProviderResult[NewsItem]:
        return ProviderResult(
            provider=self.descriptor,
            items=(
                NewsItem(
                    id="news-1",
                    external_id=None,
                    source=self.descriptor.provider_id,
                    source_type="company",
                    title="Offline news",
                    summary="",
                    content=None,
                    url=None,
                    published_at=since,
                    fetched_at=until,
                    language="en",
                    content_hash="hash",
                    related_symbols=(security.symbol,),
                ),
            )[:limit],
        )


class FakeProfileProvider:
    descriptor = descriptor(ProviderCapability.PROFILE)

    def fetch_profile(
        self, security: Security
    ) -> ProviderResult[SecurityProfile]:
        return ProviderResult(
            provider=self.descriptor,
            items=(profile(symbol=security.symbol),),
        )


def test_fake_providers_implement_synchronous_standard_contracts() -> None:
    security = Security(market="us", symbol="AAPL", name="Apple")
    quote_result = FakeQuoteProvider().fetch_quotes((security,))
    news_result = FakeNewsProvider().fetch_news(
        security,
        NOW - timedelta(days=1),
        NOW,
        1,
    )
    profile_result = FakeProfileProvider().fetch_profile(security)

    assert isinstance(quote_result.items[0], MarketSnapshot)
    assert isinstance(news_result.items[0], NewsItem)
    assert isinstance(profile_result.items[0], SecurityProfile)
    assert all(isinstance(result, ProviderResult) for result in (
        quote_result,
        news_result,
        profile_result,
    ))
    assert QuoteProvider._is_protocol is True
    assert NewsProvider._is_protocol is True
    assert ProfileProvider._is_protocol is True
