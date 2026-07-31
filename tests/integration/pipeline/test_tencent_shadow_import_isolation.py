from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[3]
ONLINE_MODULE = "daily_report_agent.providers.tencent.online_transport"
STORAGE_MODULES = {
    "daily_report_agent.storage.database",
    "daily_report_agent.storage.circuit_breaker",
}


def test_new_orchestration_import_constructs_no_online_or_storage_boundary() -> None:
    code = f"""
import sys
from daily_report_agent.pipeline.tencent_provider_shadow import (
    TencentProviderShadowOrchestrator,
    build_tencent_shadow_registry,
)
assert TencentProviderShadowOrchestrator
assert build_tencent_shadow_registry
assert {ONLINE_MODULE!r} not in sys.modules
assert not ({STORAGE_MODULES!r} & set(sys.modules))
assert 'requests' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("dry_run", "shadow_enabled", "storage_enabled"),
    [
        (True, True, True),
        (False, False, True),
        (False, True, False),
    ],
)
def test_closed_paths_do_not_load_online_transport_in_clean_process(
    tmp_path: Path,
    dry_run: bool,
    shadow_enabled: bool,
    storage_enabled: bool,
) -> None:
    code = f"""
import sys
from datetime import datetime, timezone
from daily_report_agent.pipeline.context import RunContext
from daily_report_agent.pipeline.tencent_quote_shadow import maybe_run_tencent_quote_shadow
assert {ONLINE_MODULE!r} not in sys.modules
context = RunContext('run', datetime.now(timezone.utc), {storage_enabled!r})
result = maybe_run_tencent_quote_shadow(
    config={{'providers': {{'tencent_quote': {{'shadow_enabled': {shadow_enabled!r}}}}}}},
    watchlist=[{{'market': 'cn', 'symbol': '600519', 'name': 'name'}}],
    run_context=context,
    dry_run={dry_run!r},
)
assert result.status in {{'disabled', 'skipped'}}
assert {ONLINE_MODULE!r} not in sys.modules
"""
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_main_dry_run_with_shadow_enabled_stays_offline_and_loads_no_transport(
    tmp_path: Path,
) -> None:
    database = tmp_path / "never-created.sqlite"
    reports = tmp_path / "reports"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
watchlist:
  - {{market: cn, symbol: '600519', name: 测试}}
storage:
  enabled: true
  path: {database}
providers:
  tencent_quote:
    shadow_enabled: true
notify:
  email: false
""".strip(),
        encoding="utf-8",
    )
    code = f"""
import socket
import sys
import urllib.request
from daily_report_agent import main
def fail(*args, **kwargs):
    raise AssertionError('dry-run attempted an online side effect')
socket.socket = fail
urllib.request.urlopen = fail
urllib.request.OpenerDirector.open = fail
main.load_env = fail
main.report.REPORTS_DIR = {str(reports)!r}
assert main.run({str(config)!r}, do_notify=True, dry_run=True)
assert {ONLINE_MODULE!r} not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not database.exists()


@pytest.mark.parametrize(
    ("allow", "include_path", "dry_run"),
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_provider_shadow_closed_gate_does_not_load_transport_or_storage(
    tmp_path: Path,
    allow: bool,
    include_path: bool,
    dry_run: bool,
) -> None:
    shadow_database = tmp_path / "never-created-shadow.sqlite"
    config = tmp_path / "config.yaml"
    path_line = (
        f"  provider_shadow_database_path: {shadow_database}\n"
        if include_path
        else ""
    )
    config.write_text(
        (
            "pipeline:\n"
            "  data_route: provider_shadow\n"
            "  max_provider_calls: 1\n"
            f"{path_line}"
            "providers:\n"
            "  tencent_quote:\n"
            "    shadow_enabled: true\n"
            "watchlist:\n"
            "  - {market: cn, symbol: '600519', name: 测试}\n"
        ),
        encoding="utf-8",
    )
    code = f"""
import sys
from daily_report_agent import main
try:
    main.run(
        {str(config)!r},
        do_notify=False,
        dry_run={dry_run!r},
        allow_provider_shadow={allow!r},
    )
except ValueError:
    pass
else:
    raise AssertionError('closed gate must reject')
assert {ONLINE_MODULE!r} not in sys.modules
assert not ({STORAGE_MODULES!r} & set(sys.modules))
assert 'requests' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not shadow_database.exists()
