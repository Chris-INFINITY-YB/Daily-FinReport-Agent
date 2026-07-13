from datetime import datetime, timedelta, timezone

import pytest

from daily_report_agent.storage.database import Database, RepositoryError
from daily_report_agent.storage.repositories import PipelineRunRepository


STARTED = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)
FINISHED = STARTED + timedelta(minutes=2)


def test_start_run_has_running_status_and_aware_time(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        record = PipelineRunRepository(connection).start_run(
            "run-1", STARTED, dry_run=False, config_hash="hash", app_version="0.1"
        )

    assert record.status == "running"
    assert record.started_at == STARTED
    assert record.started_at.utcoffset() is not None
    assert record.dry_run is False


@pytest.mark.parametrize("status", ["success", "partial", "failed"])
def test_run_can_finish_in_each_final_status(
    storage_db: Database,
    status: str,
) -> None:
    with storage_db.transaction() as connection:
        repository = PipelineRunRepository(connection)
        repository.start_run(f"run-{status}", STARTED, dry_run=True)
        record = repository.finish_run(
            f"run-{status}",
            status,
            FINISHED,
            created_news=2,
            created_snapshots=3,
        )

    assert record.status == status
    assert record.finished_at == FINISHED
    assert record.created_news == 2
    assert record.created_snapshots == 3


def test_finish_missing_run_is_rejected(storage_db: Database) -> None:
    with pytest.raises(RepositoryError, match="不存在"):
        with storage_db.transaction() as connection:
            PipelineRunRepository(connection).finish_run(
                "missing", "failed", FINISHED
            )


def test_finished_run_cannot_be_finished_again(storage_db: Database) -> None:
    with storage_db.transaction() as connection:
        repository = PipelineRunRepository(connection)
        repository.start_run("run-1", STARTED, dry_run=False)
        repository.finish_run("run-1", "success", FINISHED)

    with pytest.raises(RepositoryError, match="已结束"):
        with storage_db.transaction() as connection:
            PipelineRunRepository(connection).finish_run(
                "run-1", "failed", FINISHED
            )

    with pytest.raises(RepositoryError):
        with storage_db.transaction() as connection:
            PipelineRunRepository(connection).start_run(
                "run-1", FINISHED, dry_run=False
            )


def test_pipeline_error_summary_rejects_traceback_or_credentials(
    storage_db: Database,
) -> None:
    with storage_db.transaction() as connection:
        repository = PipelineRunRepository(connection)
        repository.start_run("unsafe", STARTED, dry_run=False)
        with pytest.raises(ValueError, match="安全摘要"):
            repository.finish_run(
                "unsafe",
                "failed",
                FINISHED,
                error_summary="Traceback\ntoken=secret",
            )
