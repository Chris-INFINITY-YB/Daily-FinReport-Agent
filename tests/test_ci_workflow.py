from __future__ import annotations

from pathlib import Path
import socket
import urllib.request

import pytest
import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "offline-ci.yml"


def _workflow() -> dict[str, object]:
    payload = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_offline_ci_uses_supported_python_matrix_and_read_only_permissions() -> None:
    payload = _workflow()
    permissions = payload["permissions"]
    assert permissions == {"contents": "read"}

    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    offline = jobs["offline"]
    assert isinstance(offline, dict)
    strategy = offline["strategy"]
    assert isinstance(strategy, dict)
    matrix = strategy["matrix"]
    assert isinstance(matrix, dict)
    assert matrix["python-version"] == ["3.10", "3.13"]


def test_offline_ci_runs_required_gates_without_online_extras_or_secrets() -> None:
    payload = _workflow()
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    offline = jobs["offline"]
    assert isinstance(offline, dict)
    steps = offline["steps"]
    assert isinstance(steps, list)
    commands = "\n".join(
        step["run"]
        for step in steps
        if isinstance(step, dict) and "run" in step
    )
    source = WORKFLOW.read_text(encoding="utf-8")

    assert 'pip install --disable-pip-version-check -e ".[test]"' in commands
    assert "python -m pytest -q" in commands
    assert "python -m compileall -q daily_report_agent scripts tests" in commands
    assert "git diff --check" in commands
    assert "git status --short --untracked-files=all" in commands
    assert ".[online" not in commands
    assert "--allow-network" not in source
    assert "secrets." not in source
    assert "API_KEY" not in source


def test_offline_ci_redirects_bytecode_outside_the_repository() -> None:
    payload = _workflow()
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    offline = jobs["offline"]
    assert isinstance(offline, dict)
    job_environment = offline["env"]
    assert isinstance(job_environment, dict)

    assert job_environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTHONPYCACHEPREFIX" not in job_environment
    assert all(
        "runner." not in str(value) for value in job_environment.values()
    )

    steps = offline["steps"]
    assert isinstance(steps, list)
    compile_step = next(
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("run")
        == "python -m compileall -q daily_report_agent scripts tests"
    )
    compile_environment = compile_step["env"]
    assert isinstance(compile_environment, dict)
    pycache_prefix = str(compile_environment["PYTHONPYCACHEPREFIX"])
    assert pycache_prefix == (
        "${{ runner.temp }}/daily-report-agent-pycache-"
        "${{ matrix.python-version }}"
    )
    assert pycache_prefix.startswith("${{ runner.temp }}/")
    assert "${{ matrix.python-version }}" in pycache_prefix


def test_pytest_default_gate_blocks_socket_and_urllib() -> None:
    with pytest.raises(AssertionError, match="ordinary pytest must remain offline"):
        socket.socket()
    with pytest.raises(AssertionError, match="ordinary pytest must remain offline"):
        urllib.request.urlopen("https://example.invalid")
