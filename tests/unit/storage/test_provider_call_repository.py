from datetime import datetime, timedelta, timezone

import pytest

from daily_report_agent.storage.database import Database, RepositoryError
from daily_report_agent.storage.repositories import (
    PipelineRunRepository,
    ProviderCallRepository,
)
from daily_report_agent.storage.serializers import sha256_text


STARTED = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)
FINISHED = STARTED + timedelta(seconds=1)


def _start_run(connection, run_id: str) -> None:
    PipelineRunRepository(connection).start_run(run_id, STARTED, dry_run=False)


@pytest.mark.parametrize("status", ["success", "empty", "failed", "skipped"])
def test_provider_call_supports_each_final_status(
    storage_db: Database,
    status: str,
) -> None:
    with storage_db.transaction() as connection:
        _start_run(connection, f"run-{status}")
        repository = ProviderCallRepository(connection)
        call_id = repository.start_call(
            f"run-{status}",
            "fixture",
            "fetch",
            STARTED,
            request_fingerprint=sha256_text("safe request"),
        )
        record = repository.finish_call(
            call_id,
            status,
            FINISHED,
            duration_ms=100,
            item_count=0,
            retry_count=1,
        )

    assert record.status == status
    assert record.duration_ms == 100
    assert record.retry_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("duration_ms", -1), ("item_count", -1), ("retry_count", -1)],
)
def test_provider_call_rejects_negative_counters(
    storage_db: Database,
    field: str,
    value: int,
) -> None:
    with storage_db.transaction() as connection:
        _start_run(connection, f"run-{field}")
        repository = ProviderCallRepository(connection)
        call_id = repository.start_call(f"run-{field}", "fixture", "fetch", STARTED)
        with pytest.raises(ValueError, match="不得为负数"):
            repository.finish_call(
                call_id,
                "failed",
                FINISHED,
                **{field: value},
            )


def test_provider_call_requires_existing_run(storage_db: Database) -> None:
    with pytest.raises(RepositoryError) as error:
        with storage_db.transaction() as connection:
            ProviderCallRepository(connection).start_call(
                "missing", "fixture", "fetch", STARTED
            )

    assert error.value.__cause__ is not None


def test_safe_error_summary_is_saved_and_traceback_or_secret_is_rejected(
    storage_db: Database,
) -> None:
    with storage_db.transaction() as connection:
        _start_run(connection, "run-safe")
        repository = ProviderCallRepository(connection)
        call_id = repository.start_call("run-safe", "fixture", "fetch", STARTED)
        record = repository.finish_call(
            call_id,
            "failed",
            FINISHED,
            error_severity="warning",
            error_category="network",
            error_code="TIMEOUT",
            error_message="上游请求超时",
        )

    assert record.error_message == "上游请求超时"
    assert record.error_code == "TIMEOUT"

    with storage_db.transaction() as connection:
        _start_run(connection, "run-unsafe")
        repository = ProviderCallRepository(connection)
        unsafe_id = repository.start_call("run-unsafe", "fixture", "fetch", STARTED)
        with pytest.raises(ValueError, match="安全摘要"):
            repository.finish_call(
                unsafe_id,
                "failed",
                FINISHED,
                error_message="Traceback\napi_key=secret",
            )
        with pytest.raises(ValueError, match="安全的短标识"):
            repository.finish_call(
                unsafe_id,
                "failed",
                FINISHED,
                error_code="api_key=secret",
            )
