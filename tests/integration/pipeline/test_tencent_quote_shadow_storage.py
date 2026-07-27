from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from daily_report_agent.config import TencentQuoteShadowSettings
from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.security import Security
from daily_report_agent.pipeline.context import RunContext
from daily_report_agent.pipeline.tencent_quote_shadow import (
    SQLiteTencentQuoteShadowStore,
    build_request_fingerprint,
    run_tencent_quote_shadow,
)
from daily_report_agent.providers.contracts import ProviderResult
from daily_report_agent.providers.tencent.constants import TENCENT_QUOTE_DESCRIPTOR
from daily_report_agent.storage.database import Database
from daily_report_agent.storage.repositories import PipelineRunRepository
from daily_report_agent.storage.repositories import MarketSnapshotRepository


WHEN = datetime(2026, 7, 14, 15, 0, tzinfo=timezone(timedelta(hours=8)))


class Provider:
    descriptor = TENCENT_QUOTE_DESCRIPTOR

    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0

    def fetch_quotes(self, securities):
        self.calls += 1
        return self.result


def _context(database: Database) -> RunContext:
    with database.transaction() as connection:
        PipelineRunRepository(connection).start_run("shadow-run", WHEN, dry_run=False)
    return RunContext(
        run_id="shadow-run",
        started_at=WHEN,
        storage_enabled=True,
        database=database,
        pipeline_run_id="shadow-run",
    )


def test_sqlite_shadow_is_idempotent_preserves_null_zero_and_pipeline_status(
    storage_db: Database,
) -> None:
    context = _context(storage_db)
    securities = (Security("cn", "600519", "贵州茅台"),)
    snapshot = MarketSnapshot(
        symbol="600519",
        observed_at=WHEN,
        source="tencent-finance",
        price=None,
        previous_close=0.0,
        pct_change=0.0,
        volume=None,
        currency="CNY",
    )
    provider = Provider(
        ProviderResult(provider=TENCENT_QUOTE_DESCRIPTOR, items=(snapshot,))
    )
    store = SQLiteTencentQuoteShadowStore(context)
    arguments = dict(
        securities=securities,
        run_context=context,
        settings=TencentQuoteShadowSettings(True, 5, 20, 20),
        store=store,
        provider_factory=lambda settings: provider,
        clock=lambda: WHEN,
        monotonic=lambda: 10.0,
    )
    first = run_tencent_quote_shadow(**arguments)
    second = run_tencent_quote_shadow(**arguments)

    with closing(storage_db.connect()) as connection:
        connection.row_factory = sqlite3.Row
        snapshot_row = connection.execute("SELECT * FROM market_snapshots").fetchone()
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("securities", "market_snapshots", "provider_calls", "raw_responses")
        }
        calls = connection.execute(
            "SELECT status, item_count, retry_count, operation FROM provider_calls ORDER BY id"
        ).fetchall()
        run_status = connection.execute(
            "SELECT status FROM pipeline_runs WHERE run_id = 'shadow-run'"
        ).fetchone()[0]
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert first.created_snapshot_count == 1
    assert second.created_snapshot_count == 0
    assert first.received_count == second.received_count == 1
    assert provider.calls == 2
    assert counts == {
        "securities": 1,
        "market_snapshots": 1,
        "provider_calls": 2,
        "raw_responses": 0,
    }
    assert [(row["status"], row["item_count"], row["retry_count"], row["operation"]) for row in calls] == [
        ("success", 1, 0, "quote_shadow"),
        ("success", 1, 0, "quote_shadow"),
    ]
    assert snapshot_row["price"] is None
    assert snapshot_row["previous_close"] == 0.0
    assert snapshot_row["pct_change"] == 0.0
    assert snapshot_row["source"] == "tencent-finance"
    assert snapshot_row["raw_response_id"] is None
    assert snapshot_row["observed_at"].endswith("Z")
    assert snapshot_row["fetched_at"].endswith("Z")
    assert run_status == "running"
    assert "price_windows" not in table_names


def test_single_snapshot_transaction_failure_rolls_back_only_that_item(
    storage_db: Database,
    monkeypatch,
) -> None:
    context = _context(storage_db)
    securities = (
        Security("cn", "600519", "贵州茅台"),
        Security("cn", "000001", "平安银行"),
    )
    snapshots = tuple(
        MarketSnapshot(symbol, WHEN, "tencent-finance", price=10.0)
        for symbol in ("600519", "000001")
    )
    provider = Provider(
        ProviderResult(provider=TENCENT_QUOTE_DESCRIPTOR, items=snapshots)
    )
    original = MarketSnapshotRepository.insert_or_get_snapshot

    def fail_one(self, security_id, snapshot, fetched_at):
        if snapshot.symbol == "600519":
            raise RuntimeError("unsafe sql details")
        return original(self, security_id, snapshot, fetched_at)

    monkeypatch.setattr(MarketSnapshotRepository, "insert_or_get_snapshot", fail_one)
    result = run_tencent_quote_shadow(
        securities=securities,
        run_context=context,
        settings=TencentQuoteShadowSettings(True, 5, 20, 20),
        store=SQLiteTencentQuoteShadowStore(context),
        provider_factory=lambda settings: provider,
        clock=lambda: WHEN,
        monotonic=lambda: 10.0,
    )

    with closing(storage_db.connect()) as connection:
        symbols = [
            row[0]
            for row in connection.execute(
                """
                SELECT s.symbol FROM market_snapshots ms
                JOIN securities s ON s.id = ms.security_id
                """
            )
        ]
        call_status = connection.execute(
            "SELECT status FROM provider_calls"
        ).fetchone()[0]
    assert result.status == "partial"
    assert result.received_count == 2
    assert result.created_snapshot_count == 1
    assert result.issue_count == 1
    assert symbols == ["000001"]
    assert call_status == "success"


def test_metric_output_failure_preserves_provider_call_fingerprint_and_pipeline(
    storage_db: Database,
) -> None:
    context = _context(storage_db)
    securities = (Security("cn", "600519", "贵州茅台"),)
    snapshot = MarketSnapshot(
        symbol="600519",
        observed_at=WHEN,
        source="tencent-finance",
        price=10.0,
    )
    provider = Provider(
        ProviderResult(provider=TENCENT_QUOTE_DESCRIPTOR, items=(snapshot,))
    )

    def fail_metric(event) -> None:
        raise RuntimeError(
            "https://provider.invalid response body token=secret symbol=600519"
        )

    result = run_tencent_quote_shadow(
        securities=securities,
        run_context=context,
        settings=TencentQuoteShadowSettings(True, 5, 20, 20),
        store=SQLiteTencentQuoteShadowStore(context),
        provider_factory=lambda settings: provider,
        clock=lambda: WHEN,
        monotonic=lambda: 10.0,
        metric_emitter=fail_metric,
    )

    with closing(storage_db.connect()) as connection:
        call = connection.execute(
            """
            SELECT status, item_count, retry_count, request_fingerprint
            FROM provider_calls
            """
        ).fetchone()
        run_status = connection.execute(
            "SELECT status FROM pipeline_runs WHERE run_id = 'shadow-run'"
        ).fetchone()[0]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM market_snapshots"
        ).fetchone()[0]

    assert result.status == "success"
    assert provider.calls == 1
    assert tuple(call) == (
        "success",
        1,
        0,
        build_request_fingerprint(securities),
    )
    assert run_status == "running"
    assert snapshot_count == 1
