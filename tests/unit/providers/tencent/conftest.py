from __future__ import annotations

from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parents[3] / "fixtures" / "providers" / "tencent"


@pytest.fixture
def tencent_fixture():
    def load(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return load
