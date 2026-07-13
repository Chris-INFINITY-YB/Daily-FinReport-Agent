"""SQLite 连接和事务生命周期管理。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class StorageError(RuntimeError):
    """存储层基础异常；底层异常通过 ``__cause__`` 保留。"""


class RepositoryError(StorageError):
    """Repository SQL 或约束操作失败。"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    @property
    def is_memory(self) -> bool:
        return self.path == ":memory:"

    def connect(self) -> sqlite3.Connection:
        """创建独立连接；调用方负责关闭，且不会自动初始化 schema。"""
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if not self.is_memory:
                connection.execute("PRAGMA journal_mode = WAL")
            return connection
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise StorageError(f"无法连接 SQLite 数据库: {self.path}") from exc

    def initialize(self) -> None:
        """在单一事务中升级到最新 schema；重复调用安全。"""
        from .migrations import apply_migrations, load_migrations

        with self.transaction() as connection:
            apply_migrations(connection, load_migrations())

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """提供明确的 commit/rollback 边界并在结束时关闭连接。"""
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
        except sqlite3.Error as exc:
            connection.rollback()
            raise StorageError("SQLite 事务执行失败") from exc
        except Exception:
            connection.rollback()
            raise
        else:
            try:
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise StorageError("SQLite 事务提交失败") from exc
        finally:
            connection.close()
