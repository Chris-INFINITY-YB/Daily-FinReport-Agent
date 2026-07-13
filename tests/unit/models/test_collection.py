from daily_report_agent.models.collection import CollectedSecurityData
from daily_report_agent.models.market import PriceWindow
from daily_report_agent.models.security import Security


def test_collected_security_data_creation() -> None:
    security = Security(market="cn", symbol="600519", name="贵州茅台")
    window = PriceWindow(
        start_price=1400.0,
        end_price=1420.0,
        period_pct_change=1.43,
    )

    collected = CollectedSecurityData(
        security=security,
        profile_text="固定简介",
        price_window=window,
        raw_response_ids=(1, 2),
    )

    assert collected.security is security
    assert collected.profile_text == "固定简介"
    assert collected.news == ()
    assert collected.market_snapshots == ()
    assert collected.price_window is window
    assert collected.issues == ()
    assert collected.raw_response_ids == (1, 2)
