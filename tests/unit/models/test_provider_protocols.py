from daily_report_agent.providers.base import NewsProvider, QuoteProvider


def test_provider_contracts_are_protocols_only() -> None:
    assert QuoteProvider._is_protocol is True
    assert NewsProvider._is_protocol is True
