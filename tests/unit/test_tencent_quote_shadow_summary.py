from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_report_agent.models.market import MarketSnapshot
from daily_report_agent.models.security import Security
from daily_report_agent.storage.database import Database
from daily_report_agent.storage.repositories import (
    MarketSnapshotRepository,
    PipelineRunRepository,
    ProviderCallRepository,
    SecurityRepository,
)


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "tencent_quote_shadow_summary.py"
WHEN = datetime.now(timezone.utc)


def _module():
    spec = importlib.util.spec_from_file_location("shadow_summary_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed(path: Path) -> None:
    database = Database(path)
    database.initialize()
    with database.transaction() as connection:
        PipelineRunRepository(connection).start_run("summary-run", WHEN, dry_run=False)
        call_repository = ProviderCallRepository(connection)
        call_id = call_repository.start_call(
            "summary-run",
            "tencent-finance",
            "quote_shadow",
            WHEN,
            request_fingerprint=hashlib.sha256(b"cn:600519").hexdigest(),
        )
        call_repository.finish_call(
            call_id,
            "success",
            WHEN,
            duration_ms=120,
            item_count=1,
            retry_count=0,
        )
        security_id = SecurityRepository(connection).upsert_security(
            Security("cn", "600519", "贵州茅台")
        )
        MarketSnapshotRepository(connection).insert_or_get_snapshot(
            security_id,
            MarketSnapshot(
                "600519",
                WHEN,
                "tencent-finance",
                price=1234.56,
            ),
            WHEN,
        )


def test_summary_is_read_only_and_never_outputs_price(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "summary.sqlite"
    _seed(database_path)
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()
    module = _module()
    assert module.main(["--database", str(database_path), "--days", "7"]) == 0
    output = capsys.readouterr().out
    after = hashlib.sha256(database_path.read_bytes()).hexdigest()
    assert before == after
    assert "logical_calls: 1" in output
    assert "success: 1" in output
    assert "average_duration_ms: 120.00" in output
    assert "tencent_snapshot_rows: 1" in output
    assert "symbol_latest: 600519" in output
    assert "当前 schema 无法精确统计" in output
    assert "1234.56" not in output


def test_missing_database_returns_nonzero_without_creating_it(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.sqlite"
    assert _module().main(["--database", str(missing)]) == 2
    assert not missing.exists()
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.parametrize("days", ["0", "366", "not-a-number"])
def test_days_validation_rejects_invalid_values(days: str) -> None:
    with pytest.raises(SystemExit) as caught:
        _module().main(["--database", "unused", "--days", days])
    assert caught.value.code == 2


def test_summary_script_has_no_network_config_llm_or_mutating_operations() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "mode=ro" in source
    assert "config.yaml" not in source
    assert "load_env" not in source
    assert "socket" not in source
    assert "http" not in source.lower()
    assert "LLM" not in source
    assert "DELETE " not in source
    assert "UPDATE " not in source
    assert "VACUUM" not in source
    assert "initialize(" not in source
