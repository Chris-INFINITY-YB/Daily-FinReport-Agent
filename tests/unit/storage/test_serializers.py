from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from daily_report_agent.storage.serializers import (
    datetime_to_utc_text,
    utc_text_to_datetime,
)


def test_non_utc_datetime_converts_to_stable_utc_text() -> None:
    value = datetime(2026, 7, 13, 16, 30, tzinfo=timezone(timedelta(hours=8)))

    assert datetime_to_utc_text(value) == "2026-07-13T08:30:00.000000Z"


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="必须带时区"):
        datetime_to_utc_text(datetime(2026, 7, 13, 8, 30))


def test_microseconds_round_trip_as_aware_utc() -> None:
    value = datetime(2026, 7, 13, 8, 30, 1, 123456, tzinfo=timezone.utc)

    restored = utc_text_to_datetime(datetime_to_utc_text(value))

    assert restored == value
    assert restored.tzinfo is timezone.utc


def test_daylight_saving_offset_converts_without_local_timezone() -> None:
    value = datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))

    assert datetime_to_utc_text(value) == "2026-07-13T16:00:00.000000Z"
