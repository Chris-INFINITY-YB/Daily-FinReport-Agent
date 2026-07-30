"""M1-04A Circuit Breaker 的 SQLite 持久化与原子事务编排。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterator

from daily_report_agent.providers.circuit_breaker import (
    CircuitBreakerKey,
    CircuitBreakerPolicy,
    CircuitBreakerSnapshot,
    CircuitBreakerState,
    CircuitOutcome,
    CircuitOutcomeOrigin,
    CircuitTransition,
    create_closed_circuit,
    evaluate_circuit,
    record_circuit_failure,
    record_circuit_outcome,
    record_circuit_success,
)
from daily_report_agent.providers.routing import (
    ErrorClassification,
    RetryErrorCode,
)

from .database import Database, RepositoryError, StorageError
from .serializers import datetime_to_utc_text, utc_text_to_datetime


class CircuitStorageErrorCode(str, Enum):
    """持久化边界的封闭、安全错误码。"""

    STATE_CONFLICT = "circuit_state_conflict"
    STATE_NOT_FOUND = "circuit_state_not_found"
    STATE_CORRUPT = "circuit_state_corrupt"
    PROBE_UNAVAILABLE = "circuit_probe_unavailable"
    SQL_FAILED = "circuit_storage_failed"


class CircuitStorageError(RepositoryError):
    """不公开 SQL、数据库路径或原始 row 的持久化错误。"""

    def __init__(
        self,
        code: CircuitStorageErrorCode,
        safe_message: str,
    ) -> None:
        if not isinstance(code, CircuitStorageErrorCode):
            raise TypeError("code 必须是 CircuitStorageErrorCode")
        if not isinstance(safe_message, str) or not safe_message.strip():
            raise ValueError("safe_message 不能为空")
        self.code = code
        self.safe_message = safe_message.strip()
        super().__init__(self.safe_message)


def _validate_version(version: int) -> int:
    if not isinstance(version, int) or isinstance(version, bool):
        raise TypeError("version 必须是整数")
    if version < 0:
        raise ValueError("version 不得为负")
    return version


def _validate_aware_datetime(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} 必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} 必须是 timezone-aware datetime")
    return value


@dataclass(frozen=True, slots=True)
class PersistedCircuitBreakerSnapshot:
    """业务 Snapshot 与仅供持久化并发控制使用的 version。"""

    snapshot: CircuitBreakerSnapshot
    version: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, CircuitBreakerSnapshot):
            raise TypeError("snapshot 必须是 CircuitBreakerSnapshot")
        _validate_version(self.version)
        updated_at = _validate_aware_datetime("updated_at", self.updated_at)
        if updated_at < self.snapshot.last_transition_at:
            raise ValueError("updated_at 不得早于 snapshot.last_transition_at")


@dataclass(frozen=True, slots=True)
class PersistedCircuitTransition:
    """一次原子转换及提交后的 version。"""

    transition: CircuitTransition
    version: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.transition, CircuitTransition):
            raise TypeError("transition 必须是 CircuitTransition")
        _validate_version(self.version)
        updated_at = _validate_aware_datetime("updated_at", self.updated_at)
        if updated_at < self.transition.after.last_transition_at:
            raise ValueError("updated_at 不得早于 transition.after")


def _optional_datetime_text(value: datetime | None) -> str | None:
    return None if value is None else datetime_to_utc_text(value)


def _optional_utc_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("可选时间列必须是字符串或 NULL")
    return utc_text_to_datetime(value)


class CircuitBreakerRepository:
    """只负责 Circuit Breaker 行、序列化和 version/CAS，不自行提交。"""

    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection 必须是 sqlite3.Connection")
        self.connection = connection

    def _execute(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, parameters)
        except sqlite3.Error as exc:
            error = CircuitStorageError(
                CircuitStorageErrorCode.SQL_FAILED,
                "Circuit Breaker 持久化操作失败",
            )
            raise error from exc

    def load(
        self,
        key: CircuitBreakerKey,
    ) -> PersistedCircuitBreakerSnapshot | None:
        if not isinstance(key, CircuitBreakerKey):
            raise TypeError("key 必须是 CircuitBreakerKey")
        row = self._execute(
            """
            SELECT provider_id, operation, state, consecutive_failures,
                   opened_at, open_until, last_transition_at,
                   last_failure_at, last_success_at, last_error_code,
                   half_open_probe_active, version, updated_at
            FROM provider_circuit_breakers
            WHERE provider_id = ? AND operation = ?
            """,
            (key.provider_id, key.operation),
        ).fetchone()
        if row is None:
            return None
        return self._deserialize(row)

    def create_closed(
        self,
        key: CircuitBreakerKey,
        *,
        now: datetime,
    ) -> PersistedCircuitBreakerSnapshot:
        snapshot = create_closed_circuit(key, now=now)
        parameters = self._snapshot_parameters(
            snapshot,
            version=0,
            updated_at=now,
        )
        self._execute(
            """
            INSERT INTO provider_circuit_breakers(
                provider_id, operation, state, consecutive_failures,
                opened_at, open_until, last_transition_at,
                last_failure_at, last_success_at, last_error_code,
                half_open_probe_active, version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, operation) DO NOTHING
            """,
            parameters,
        )
        persisted = self.load(key)
        if persisted is None:
            raise CircuitStorageError(
                CircuitStorageErrorCode.STATE_NOT_FOUND,
                "Circuit Breaker 初始化后状态不存在",
            )
        return persisted

    def compare_and_swap(
        self,
        expected: PersistedCircuitBreakerSnapshot,
        after: CircuitBreakerSnapshot,
        *,
        updated_at: datetime,
    ) -> PersistedCircuitBreakerSnapshot:
        if not isinstance(expected, PersistedCircuitBreakerSnapshot):
            raise TypeError("expected 必须是 PersistedCircuitBreakerSnapshot")
        if not isinstance(after, CircuitBreakerSnapshot):
            raise TypeError("after 必须是 CircuitBreakerSnapshot")
        if expected.snapshot.key != after.key:
            raise ValueError("CAS 不得改变 Circuit Breaker key")
        _validate_aware_datetime("updated_at", updated_at)
        next_version = expected.version + 1
        parameters = self._snapshot_parameters(
            after,
            version=next_version,
            updated_at=updated_at,
        )
        cursor = self._execute(
            """
            UPDATE provider_circuit_breakers
            SET state = ?, consecutive_failures = ?, opened_at = ?,
                open_until = ?, last_transition_at = ?,
                last_failure_at = ?, last_success_at = ?,
                last_error_code = ?, half_open_probe_active = ?,
                version = ?, updated_at = ?
            WHERE provider_id = ? AND operation = ? AND version = ?
            """,
            parameters[2:] + (
                after.key.provider_id,
                after.key.operation,
                expected.version,
            ),
        )
        if cursor.rowcount != 1:
            current = self.load(after.key)
            if current is None:
                raise CircuitStorageError(
                    CircuitStorageErrorCode.STATE_NOT_FOUND,
                    "Circuit Breaker 状态不存在",
                )
            raise CircuitStorageError(
                CircuitStorageErrorCode.STATE_CONFLICT,
                "Circuit Breaker 状态版本冲突",
            )
        persisted = self.load(after.key)
        if persisted is None:
            raise CircuitStorageError(
                CircuitStorageErrorCode.STATE_NOT_FOUND,
                "Circuit Breaker 更新后状态不存在",
            )
        if persisted.version != next_version:
            raise CircuitStorageError(
                CircuitStorageErrorCode.STATE_CONFLICT,
                "Circuit Breaker 更新版本不一致",
            )
        return persisted

    @staticmethod
    def _snapshot_parameters(
        snapshot: CircuitBreakerSnapshot,
        *,
        version: int,
        updated_at: datetime,
    ) -> tuple[object, ...]:
        if not isinstance(snapshot, CircuitBreakerSnapshot):
            raise TypeError("snapshot 必须是 CircuitBreakerSnapshot")
        _validate_version(version)
        _validate_aware_datetime("updated_at", updated_at)
        return (
            snapshot.key.provider_id,
            snapshot.key.operation,
            snapshot.state.value,
            snapshot.consecutive_failures,
            _optional_datetime_text(snapshot.opened_at),
            _optional_datetime_text(snapshot.open_until),
            datetime_to_utc_text(snapshot.last_transition_at),
            _optional_datetime_text(snapshot.last_failure_at),
            _optional_datetime_text(snapshot.last_success_at),
            (
                snapshot.last_error_code.value
                if snapshot.last_error_code is not None
                else None
            ),
            int(snapshot.half_open_probe_active),
            version,
            datetime_to_utc_text(updated_at),
        )

    @staticmethod
    def _deserialize(row: sqlite3.Row) -> PersistedCircuitBreakerSnapshot:
        try:
            probe_value = row["half_open_probe_active"]
            if type(probe_value) is not int or probe_value not in (0, 1):
                raise ValueError("非法 probe 标记")
            failures = row["consecutive_failures"]
            if type(failures) is not int:
                raise TypeError("非法失败计数")
            version = row["version"]
            if type(version) is not int:
                raise TypeError("非法 version")
            error_code_value = row["last_error_code"]
            if error_code_value is not None and not isinstance(
                error_code_value,
                str,
            ):
                raise TypeError("非法 error_code")
            snapshot = CircuitBreakerSnapshot(
                key=CircuitBreakerKey(
                    row["provider_id"],
                    row["operation"],
                ),
                state=CircuitBreakerState(row["state"]),
                consecutive_failures=failures,
                opened_at=_optional_utc_datetime(row["opened_at"]),
                open_until=_optional_utc_datetime(row["open_until"]),
                last_transition_at=utc_text_to_datetime(
                    row["last_transition_at"]
                ),
                last_failure_at=_optional_utc_datetime(
                    row["last_failure_at"]
                ),
                last_success_at=_optional_utc_datetime(
                    row["last_success_at"]
                ),
                last_error_code=(
                    RetryErrorCode(error_code_value)
                    if error_code_value is not None
                    else None
                ),
                half_open_probe_active=bool(probe_value),
            )
            return PersistedCircuitBreakerSnapshot(
                snapshot=snapshot,
                version=version,
                updated_at=utc_text_to_datetime(row["updated_at"]),
            )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            error = CircuitStorageError(
                CircuitStorageErrorCode.STATE_CORRUPT,
                "Circuit Breaker 持久化状态损坏",
            )
            raise error from exc


class SQLiteCircuitBreakerStore:
    """用 SQLite 原子事务编排 M1-04A 纯状态转换。"""

    def __init__(self, database: Database):
        if not isinstance(database, Database):
            raise TypeError("database 必须是 Database")
        self.database = database

    def load(
        self,
        key: CircuitBreakerKey,
    ) -> PersistedCircuitBreakerSnapshot | None:
        try:
            connection = self.database.connect()
            try:
                return CircuitBreakerRepository(connection).load(key)
            finally:
                connection.close()
        except CircuitStorageError:
            raise
        except StorageError as exc:
            raise self._safe_storage_error() from exc

    def load_or_create_closed(
        self,
        key: CircuitBreakerKey,
        *,
        now: datetime,
    ) -> PersistedCircuitBreakerSnapshot:
        with self._immediate_repository() as repository:
            return repository.create_closed(
                key,
                now=now,
            )

    def preflight(
        self,
        key: CircuitBreakerKey,
        policy: CircuitBreakerPolicy,
        *,
        now: datetime,
    ) -> PersistedCircuitTransition:
        if not isinstance(policy, CircuitBreakerPolicy):
            raise TypeError("policy 必须是 CircuitBreakerPolicy")
        with self._immediate_repository() as repository:
            persisted = repository.create_closed(key, now=now)
            transition = evaluate_circuit(persisted.snapshot, now=now)
            return self._persist_transition(
                repository,
                persisted,
                transition,
                now=now,
            )

    def record_success(
        self,
        key: CircuitBreakerKey,
        *,
        expected_version: int,
        now: datetime,
    ) -> PersistedCircuitTransition:
        with self._immediate_repository() as repository:
            persisted = self._load_expected(
                repository,
                key,
                expected_version,
            )
            transition = record_circuit_success(persisted.snapshot, now=now)
            return self._persist_transition(
                repository,
                persisted,
                transition,
                now=now,
            )

    def record_failure(
        self,
        key: CircuitBreakerKey,
        policy: CircuitBreakerPolicy,
        classification: ErrorClassification,
        *,
        expected_version: int,
        now: datetime,
        origin: CircuitOutcomeOrigin = CircuitOutcomeOrigin.PROVIDER,
    ) -> PersistedCircuitTransition:
        with self._immediate_repository() as repository:
            persisted = self._load_expected(
                repository,
                key,
                expected_version,
            )
            transition = record_circuit_failure(
                persisted.snapshot,
                policy,
                classification,
                now=now,
                origin=origin,
            )
            return self._persist_transition(
                repository,
                persisted,
                transition,
                now=now,
            )

    def record_outcome(
        self,
        key: CircuitBreakerKey,
        policy: CircuitBreakerPolicy,
        outcome: CircuitOutcome,
        *,
        expected_version: int,
        now: datetime,
        error_code: RetryErrorCode | None = None,
    ) -> PersistedCircuitTransition:
        with self._immediate_repository() as repository:
            persisted = self._load_expected(
                repository,
                key,
                expected_version,
            )
            transition = record_circuit_outcome(
                persisted.snapshot,
                policy,
                outcome,
                now=now,
                error_code=error_code,
            )
            return self._persist_transition(
                repository,
                persisted,
                transition,
                now=now,
            )

    @staticmethod
    def _load_expected(
        repository: CircuitBreakerRepository,
        key: CircuitBreakerKey,
        expected_version: int,
    ) -> PersistedCircuitBreakerSnapshot:
        _validate_version(expected_version)
        persisted = repository.load(key)
        if persisted is None:
            raise CircuitStorageError(
                CircuitStorageErrorCode.STATE_NOT_FOUND,
                "Circuit Breaker 状态不存在",
            )
        if persisted.version != expected_version:
            raise CircuitStorageError(
                CircuitStorageErrorCode.STATE_CONFLICT,
                "Circuit Breaker 状态版本冲突",
            )
        return persisted

    @contextmanager
    def _immediate_repository(
        self,
    ) -> Iterator[CircuitBreakerRepository]:
        try:
            with self.database.immediate_transaction() as connection:
                yield CircuitBreakerRepository(connection)
        except CircuitStorageError:
            raise
        except StorageError as exc:
            raise self._safe_storage_error() from exc

    @staticmethod
    def _safe_storage_error() -> CircuitStorageError:
        return CircuitStorageError(
            CircuitStorageErrorCode.SQL_FAILED,
            "Circuit Breaker 持久化事务失败",
        )

    @staticmethod
    def _persist_transition(
        repository: CircuitBreakerRepository,
        persisted: PersistedCircuitBreakerSnapshot,
        transition: CircuitTransition,
        *,
        now: datetime,
    ) -> PersistedCircuitTransition:
        if transition.changed:
            after = repository.compare_and_swap(
                persisted,
                transition.after,
                updated_at=now,
            )
            version = after.version
            updated_at = after.updated_at
        else:
            version = persisted.version
            updated_at = persisted.updated_at
        return PersistedCircuitTransition(
            transition=transition,
            version=version,
            updated_at=updated_at,
        )
