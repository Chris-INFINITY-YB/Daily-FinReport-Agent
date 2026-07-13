"""单次日报运行的可选存储上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daily_report_agent.storage.database import Database


@dataclass(slots=True)
class RunContext:
    run_id: str
    started_at: datetime
    storage_enabled: bool
    database: Database | None = None
    pipeline_run_id: str | None = None
    created_news: int = 0
    created_snapshots: int = 0
    storage_failed: bool = False

    @property
    def storage_active(self) -> bool:
        return (
            self.storage_enabled
            and self.database is not None
            and self.pipeline_run_id is not None
        )
