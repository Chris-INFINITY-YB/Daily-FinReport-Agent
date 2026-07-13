from pathlib import Path

from daily_report_agent.main import load_config


def test_config_loads_from_pytest_fixture(config_path: Path) -> None:
    config = load_config(str(config_path))

    assert config["watchlist"][0]["symbol"] == "TEST"
    assert config["data"]["news_days"] == 7
    assert config["notify"]["email"] is True
