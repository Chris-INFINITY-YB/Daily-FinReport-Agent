"""显式启用的 SQLite 存储旁路；导入本包不会创建数据库。"""

from .database import Database, RepositoryError, StorageError
from .circuit_breaker import (
    CircuitBreakerRepository,
    CircuitStorageError,
    CircuitStorageErrorCode,
    PersistedCircuitBreakerSnapshot,
    PersistedCircuitTransition,
    SQLiteCircuitBreakerStore,
)
from .migrations import Migration, MigrationError
from .repositories import (
    MarketSnapshotRepository,
    NewsRepository,
    PipelineRunRepository,
    ProviderCallRepository,
    RawResponseRepository,
    SecurityRepository,
)

__all__ = [
    "CircuitBreakerRepository",
    "CircuitStorageError",
    "CircuitStorageErrorCode",
    "Database",
    "MarketSnapshotRepository",
    "Migration",
    "MigrationError",
    "NewsRepository",
    "PipelineRunRepository",
    "ProviderCallRepository",
    "PersistedCircuitBreakerSnapshot",
    "PersistedCircuitTransition",
    "RawResponseRepository",
    "RepositoryError",
    "SecurityRepository",
    "SQLiteCircuitBreakerStore",
    "StorageError",
]
