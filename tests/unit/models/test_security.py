import pytest

from daily_report_agent.models.security import Security


def test_security_normalizes_market_and_symbol() -> None:
    security = Security(
        market=" US ",
        symbol=" AAPL ",
        name=" Apple ",
        aliases=("苹果",),
    )

    assert security.market == "us"
    assert security.symbol == "AAPL"
    assert security.name == "Apple"
    assert security.aliases == ("苹果",)


@pytest.mark.parametrize("market", ["hk", ""])
def test_security_rejects_unsupported_market(market: str) -> None:
    with pytest.raises(ValueError, match="market"):
        Security(market=market, symbol="TEST", name="测试标的")


def test_security_rejects_empty_symbol() -> None:
    with pytest.raises(ValueError, match="symbol"):
        Security(market="cn", symbol="   ", name="测试标的")
