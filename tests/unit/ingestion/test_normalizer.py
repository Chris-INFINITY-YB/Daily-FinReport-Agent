from datetime import datetime

from daily_report_agent.ingestion.normalizer import (
    normalize_datetime,
    normalize_missing_values,
    normalize_symbol,
    zero_to_none,
)


def test_normalizer_functions_are_deterministic_and_timezone_aware() -> None:
    normalized = normalize_datetime("2026-07-13")

    assert normalize_symbol(" 600519 ") == "600519"
    assert isinstance(normalized, datetime)
    assert normalized.utcoffset() is not None


def test_zero_to_none_never_converts_price_fields() -> None:
    assert zero_to_none(0.0, "price") == 0.0
    assert zero_to_none(0.0, "start_price") == 0.0
    assert zero_to_none(0.0, "end_price") == 0.0
    assert zero_to_none(0.0, "pct_change") is None

    normalized = normalize_missing_values(
        {"price": 0.0, "pct_change": 0.0, "volume": 0.0}
    )
    assert normalized == {"price": 0.0, "pct_change": None, "volume": None}
