from datetime import datetime, timezone

import pytest

from daily_report_agent.models.market import MarketSnapshot, PriceWindow


def test_market_snapshot_uses_none_for_unknown_values() -> None:
    snapshot = MarketSnapshot(
        symbol="AAPL",
        observed_at=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        source="fixture",
    )

    assert snapshot.price is None
    assert snapshot.previous_close is None
    assert snapshot.pct_change is None
    assert snapshot.volume is None
    assert snapshot.market_cap is None


def test_market_snapshot_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="observed_at"):
        MarketSnapshot(
            symbol="AAPL",
            observed_at=datetime(2026, 7, 13, 8, 0),
            source="fixture",
        )


def test_price_window_preserves_period_values() -> None:
    window = PriceWindow(
        start_price=98.0,
        end_price=100.0,
        period_pct_change=2.04,
    )

    assert window.start_price == 98.0
    assert window.end_price == 100.0
    assert window.period_pct_change == 2.04
