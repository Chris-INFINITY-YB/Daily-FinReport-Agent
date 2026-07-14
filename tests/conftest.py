from __future__ import annotations

from pathlib import Path
import socket
import urllib.request

import pytest

from daily_report_agent.storage.database import Database


@pytest.fixture(autouse=True)
def block_unexpected_network(monkeypatch):
    """普通 pytest 全局禁止 socket 和 urllib 在线请求。"""

    def fail(*args, **kwargs):
        raise AssertionError("ordinary pytest must remain offline")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fail)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
watchlist:
  - {market: us, symbol: TEST, name: 测试标的}
data:
  news_days: 7
  max_news_per_stock: 3
llm:
  provider: deepseek
notify:
  email: true
  telegram: true
  serverchan: true
""".strip(),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def storage_db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "agent-test.sqlite")
    database.initialize()
    return database
