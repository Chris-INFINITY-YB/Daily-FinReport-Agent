"""版本化 SQL migration 加载与事务内执行。"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from typing import Iterable

from .database import StorageError
from .serializers import datetime_to_utc_text


class MigrationError(StorageError):
    """Migration 加载或执行失败。"""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


_MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def load_migrations() -> tuple[Migration, ...]:
    sql_dir = files("daily_report_agent.storage").joinpath("sql")
    migrations = []
    for resource in sql_dir.iterdir():
        match = _MIGRATION_NAME.fullmatch(resource.name)
        if match:
            migrations.append(
                Migration(
                    version=int(match.group(1)),
                    name=match.group(2),
                    sql=resource.read_text(encoding="utf-8"),
                )
            )
    migrations.sort(key=lambda migration: migration.version)
    versions = [migration.version for migration in migrations]
    if not migrations or len(versions) != len(set(versions)):
        raise MigrationError("Migration 版本缺失或重复")
    return tuple(migrations)


def _statements(sql: str) -> Iterable[str]:
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        raise MigrationError("Migration 包含不完整 SQL")


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[Migration],
) -> None:
    """在调用方事务中应用尚未执行的 migration，不自行 commit。"""
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            row["version"]: row["name"]
            for row in connection.execute(
                "SELECT version, name FROM schema_migrations"
            )
        }
        from datetime import datetime, timezone

        for migration in migrations:
            if migration.version in applied:
                if applied[migration.version] != migration.name:
                    raise MigrationError(
                        f"Migration {migration.version} 名称与已应用记录不一致"
                    )
                continue
            for statement in _statements(migration.sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    datetime_to_utc_text(datetime.now(timezone.utc)),
                ),
            )
    except MigrationError:
        raise
    except sqlite3.Error as exc:
        raise MigrationError("Migration 执行失败") from exc
