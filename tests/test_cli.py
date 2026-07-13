from __future__ import annotations

from pathlib import Path

from daily_report_agent import main


def test_cli_parses_dry_run() -> None:
    args = main.parse_args(["--dry-run"])

    assert args.dry_run is True
    assert args.no_notify is False


def test_cli_dry_run_returns_zero(
    config_path: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(main.report, "REPORTS_DIR", str(tmp_path / "reports"))

    exit_code = main.main(["--dry-run", "--config", str(config_path)])

    assert exit_code == 0


def test_cli_invalid_config_returns_nonzero(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.yaml"

    exit_code = main.main(["--dry-run", "--config", str(missing)])

    assert exit_code != 0
    assert "FileNotFoundError" in capsys.readouterr().err


def test_cli_non_mapping_config_returns_nonzero(tmp_path: Path, capsys) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("- 不是\n- 映射\n", encoding="utf-8")

    exit_code = main.main(["--dry-run", "--config", str(invalid)])

    assert exit_code != 0
    assert "ValueError" in capsys.readouterr().err
