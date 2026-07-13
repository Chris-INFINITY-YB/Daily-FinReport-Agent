from __future__ import annotations

import socket
import sys
from pathlib import Path
from types import ModuleType

from daily_report_agent import main


def _forbidden(name: str):
    def fail(*args, **kwargs):
        raise AssertionError(f"dry-run 不得调用 {name}")

    return fail


def test_dry_run_is_offline_and_skips_llm_and_notifications(
    config_path: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(main, "load_env", _forbidden("load_env/.env"))
    monkeypatch.setattr(main, "build_llm", _forbidden("真实 LLM"))
    monkeypatch.setattr(main, "get_source", _forbidden("真实数据源"))
    monkeypatch.setattr(socket, "socket", _forbidden("网络 socket"))

    notifier = ModuleType("daily_report_agent.notifier")
    notifier.notify = _forbidden("真实通知")
    monkeypatch.setitem(sys.modules, "daily_report_agent.notifier", notifier)

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(reports_dir))

    path = Path(main.run(str(config_path), do_notify=True, dry_run=True))
    content = path.read_text(encoding="utf-8")

    assert path.parent == reports_dir
    assert "测试标的 (TEST)" in content
    assert "dry-run 模式未调用 LLM" in content
    assert "免责声明" in content
