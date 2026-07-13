import hashlib
from contextlib import closing
from datetime import datetime, timezone

import pytest

from daily_report_agent.storage.database import Database, RepositoryError
from daily_report_agent.storage.repositories import (
    PipelineRunRepository,
    ProviderCallRepository,
    RawResponseRepository,
)


WHEN = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)


def _provider_call_id(connection, run_id: str = "run-raw") -> int:
    PipelineRunRepository(connection).start_run(run_id, WHEN, dry_run=False)
    return ProviderCallRepository(connection).start_call(
        run_id, "fixture", "fetch", WHEN
    )


def test_redacted_response_computes_sha256(storage_db: Database) -> None:
    body = '{"status":"redacted"}'
    with storage_db.transaction() as connection:
        repository = RawResponseRepository(connection)
        response_id = repository.insert_redacted_response(
            _provider_call_id(connection),
            0,
            WHEN,
            body,
            http_status=200,
            content_type="application/json",
        )

    with closing(storage_db.connect()) as connection:
        record = RawResponseRepository(connection).get_by_id(response_id)

    assert record is not None
    assert record.body == body.encode()
    assert record.body_sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert record.is_redacted is True


def test_duplicate_sequence_is_rejected(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        call_id = _provider_call_id(connection)
        RawResponseRepository(connection).insert_redacted_response(
            call_id, 0, WHEN, "first"
        )

    with pytest.raises(RepositoryError):
        with storage_db.transaction() as connection:
            RawResponseRepository(connection).insert_redacted_response(
                call_id, 0, WHEN, "duplicate"
            )

    with closing(storage_db.connect()) as connection:
        count = connection.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]
    assert count == 1


def test_unredacted_or_mapping_body_is_rejected(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        call_id = _provider_call_id(connection)
        repository = RawResponseRepository(connection)
        with pytest.raises(ValueError, match="未脱敏"):
            repository.insert_redacted_response(
                call_id, 0, WHEN, "secret", is_redacted=False
            )
        with pytest.raises(TypeError, match="映射响应"):
            repository.insert_redacted_response(
                call_id, 1, WHEN, {"api_key": "secret"}  # type: ignore[arg-type]
            )


def test_raw_response_is_removed_by_transaction_rollback(storage_db: Database) -> None:
    with pytest.raises(RuntimeError, match="force rollback"):
        with storage_db.transaction() as connection:
            call_id = _provider_call_id(connection)
            RawResponseRepository(connection).insert_redacted_response(
                call_id, 0, WHEN, "safe"
            )
            raise RuntimeError("force rollback")

    with closing(storage_db.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM provider_calls").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 0
