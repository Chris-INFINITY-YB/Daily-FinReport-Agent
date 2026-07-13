"""按领域拆分的 SQLite Repository；所有写入服从调用方事务。"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from daily_report_agent.models.market import MarketSnapshot, PriceWindow
from daily_report_agent.models.news import NewsItem
from daily_report_agent.models.security import Security

from .database import RepositoryError
from .serializers import (
    datetime_to_utc_text,
    sha256_bytes,
    utc_text_to_datetime,
)


def _now_text() -> str:
    return datetime_to_utc_text(datetime.now(timezone.utc))


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class _Repository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, parameters)
        except sqlite3.Error as exc:
            raise RepositoryError("Repository SQL 执行失败") from exc


class SecurityRepository(_Repository):
    def upsert_security(self, security: Security) -> int:
        now = _now_text()
        self._execute(
            """
            INSERT INTO securities(
                market, symbol, exchange, name, currency, industry,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, symbol) DO UPDATE SET
                name = excluded.name,
                exchange = COALESCE(excluded.exchange, securities.exchange),
                currency = COALESCE(excluded.currency, securities.currency),
                industry = COALESCE(excluded.industry, securities.industry),
                updated_at = excluded.updated_at
            """,
            (
                security.market,
                security.symbol,
                _optional_text(security.exchange),
                security.name,
                _optional_text(security.currency),
                _optional_text(security.industry),
                now,
                now,
            ),
        )
        row = self._execute(
            "SELECT id FROM securities WHERE market = ? AND symbol = ?",
            (security.market, security.symbol),
        ).fetchone()
        if row is None:
            raise RepositoryError("Security upsert 后无法读取主键")
        return int(row["id"])

    def get_by_market_symbol(self, market: str, symbol: str) -> Security | None:
        row = self._execute(
            "SELECT * FROM securities WHERE market = ? AND symbol = ?",
            (market.strip().lower(), symbol.strip()),
        ).fetchone()
        if row is None:
            return None
        aliases = tuple(
            alias_row["alias"]
            for alias_row in self._execute(
                """
                SELECT alias FROM security_aliases
                WHERE security_id = ? ORDER BY alias
                """,
                (row["id"],),
            )
        )
        return Security(
            market=row["market"],
            symbol=row["symbol"],
            name=row["name"],
            exchange=row["exchange"],
            currency=row["currency"],
            industry=row["industry"],
            aliases=aliases,
        )

    def replace_aliases(self, security_id: int, aliases: tuple[str, ...]) -> None:
        if not isinstance(aliases, tuple):
            raise TypeError("aliases 必须是 tuple[str, ...]")
        if self._execute(
            "SELECT 1 FROM securities WHERE id = ?", (security_id,)
        ).fetchone() is None:
            raise RepositoryError(f"Security 不存在: {security_id}")

        normalized = tuple(
            sorted({alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()})
        )
        if len(normalized) != len(aliases):
            raise ValueError("aliases 不能包含空值、非字符串或重复项")
        self._execute("DELETE FROM security_aliases WHERE security_id = ?", (security_id,))
        now = _now_text()
        for alias in normalized:
            self._execute(
                """
                INSERT INTO security_aliases(security_id, alias, alias_type, created_at)
                VALUES (?, ?, 'unknown', ?)
                """,
                (security_id, alias, now),
            )


class NewsRepository(_Repository):
    def _find_identity(self, news: NewsItem) -> sqlite3.Row | None:
        external_id = _optional_text(news.external_id)
        if external_id is not None:
            return self._execute(
                "SELECT id FROM news_items WHERE source = ? AND external_id = ?",
                (news.source, external_id),
            ).fetchone()
        return self._execute(
            """
            SELECT id FROM news_items
            WHERE source = ? AND external_id IS NULL AND content_hash = ?
            """,
            (news.source, news.content_hash),
        ).fetchone()

    def insert_or_get_news(self, news: NewsItem) -> tuple[str, bool]:
        existing = self._find_identity(news)
        if existing is not None:
            return str(existing["id"]), False

        self._execute(
            """
            INSERT INTO news_items(
                id, external_id, source, source_type, title, summary, content,
                url, published_at, fetched_at, language, content_hash,
                source_reliability, raw_response_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                news.id,
                _optional_text(news.external_id),
                news.source,
                news.source_type,
                news.title,
                news.summary,
                news.content,
                news.url,
                datetime_to_utc_text(news.published_at),
                datetime_to_utc_text(news.fetched_at),
                news.language,
                news.content_hash,
                news.source_reliability,
            ),
        )
        return news.id, True

    def get_by_id(self, news_id: str) -> NewsItem | None:
        row = self._execute(
            "SELECT * FROM news_items WHERE id = ?", (news_id,)
        ).fetchone()
        if row is None:
            return None
        related_symbols = tuple(
            link["symbol"]
            for link in self._execute(
                """
                SELECT s.symbol
                FROM news_security_links AS l
                JOIN securities AS s ON s.id = l.security_id
                WHERE l.news_id = ?
                ORDER BY s.symbol
                """,
                (news_id,),
            )
        )
        return NewsItem(
            id=row["id"],
            external_id=row["external_id"],
            source=row["source"],
            source_type=row["source_type"],
            title=row["title"],
            summary=row["summary"],
            content=row["content"],
            url=row["url"],
            published_at=utc_text_to_datetime(row["published_at"]),
            fetched_at=utc_text_to_datetime(row["fetched_at"]),
            language=row["language"],
            content_hash=row["content_hash"],
            related_symbols=related_symbols,
            source_reliability=row["source_reliability"],
        )

    def link_to_security(
        self,
        news_id: str,
        security_id: int,
        relation_type: str = "mentioned",
        confidence: float | None = None,
    ) -> None:
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        relation_type = relation_type.strip()
        if not relation_type:
            raise ValueError("relation_type 不能为空")
        self._execute(
            """
            INSERT INTO news_security_links(
                news_id, security_id, relation_type, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(news_id, security_id) DO NOTHING
            """,
            (news_id, security_id, relation_type, confidence, _now_text()),
        )


class MarketSnapshotRepository(_Repository):
    def insert_or_get_snapshot(
        self,
        security_id: int,
        snapshot: MarketSnapshot,
        fetched_at: datetime,
    ) -> tuple[int, bool]:
        if isinstance(snapshot, PriceWindow) or not isinstance(snapshot, MarketSnapshot):
            raise TypeError("只接受 MarketSnapshot，不接受 PriceWindow")
        observed_at = datetime_to_utc_text(snapshot.observed_at)
        existing = self._execute(
            """
            SELECT id FROM market_snapshots
            WHERE security_id = ? AND source = ? AND observed_at = ?
            """,
            (security_id, snapshot.source, observed_at),
        ).fetchone()
        if existing is not None:
            return int(existing["id"]), False

        cursor = self._execute(
            """
            INSERT INTO market_snapshots(
                security_id, observed_at, price, previous_close, pct_change,
                volume, amount, turnover, pe_ttm, pb, market_cap, currency,
                source, fetched_at, raw_response_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                security_id,
                observed_at,
                snapshot.price,
                snapshot.previous_close,
                snapshot.pct_change,
                snapshot.volume,
                snapshot.amount,
                snapshot.turnover,
                snapshot.pe_ttm,
                snapshot.pb,
                snapshot.market_cap,
                snapshot.currency,
                snapshot.source,
                datetime_to_utc_text(fetched_at),
            ),
        )
        return int(cursor.lastrowid), True

    def get_by_id(self, snapshot_id: int) -> MarketSnapshot | None:
        row = self._execute(
            """
            SELECT ms.*, s.symbol
            FROM market_snapshots AS ms
            JOIN securities AS s ON s.id = ms.security_id
            WHERE ms.id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return MarketSnapshot(
            symbol=row["symbol"],
            observed_at=utc_text_to_datetime(row["observed_at"]),
            price=row["price"],
            previous_close=row["previous_close"],
            pct_change=row["pct_change"],
            volume=row["volume"],
            amount=row["amount"],
            turnover=row["turnover"],
            pe_ttm=row["pe_ttm"],
            pb=row["pb"],
            market_cap=row["market_cap"],
            currency=row["currency"],
            source=row["source"],
        )


@dataclass(frozen=True, slots=True)
class PipelineRunRecord:
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    dry_run: bool
    config_hash: str | None
    app_version: str | None
    error_summary: str | None
    created_news: int
    created_snapshots: int


class PipelineRunRepository(_Repository):
    _FINAL_STATUSES = {"success", "partial", "failed"}

    def start_run(
        self,
        run_id: str,
        started_at: datetime,
        *,
        dry_run: bool,
        config_hash: str | None = None,
        app_version: str | None = None,
    ) -> PipelineRunRecord:
        run_id = run_id.strip()
        if not run_id:
            raise ValueError("run_id 不能为空")
        self._execute(
            """
            INSERT INTO pipeline_runs(
                run_id, started_at, status, dry_run, config_hash, app_version
            ) VALUES (?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id,
                datetime_to_utc_text(started_at),
                int(bool(dry_run)),
                _optional_text(config_hash),
                _optional_text(app_version),
            ),
        )
        record = self.get_run(run_id)
        if record is None:
            raise RepositoryError("Pipeline run 创建后无法读取")
        return record

    def finish_run(
        self,
        run_id: str,
        status: str,
        finished_at: datetime,
        *,
        error_summary: str | None = None,
        created_news: int = 0,
        created_snapshots: int = 0,
    ) -> PipelineRunRecord:
        if status not in self._FINAL_STATUSES:
            raise ValueError("finish_run 状态只允许 success、partial 或 failed")
        if created_news < 0 or created_snapshots < 0:
            raise ValueError("创建数量不得为负数")
        existing = self.get_run(run_id)
        if existing is None:
            raise RepositoryError(f"Pipeline run 不存在: {run_id}")
        if existing.status != "running":
            raise RepositoryError(f"Pipeline run 已结束: {run_id}")
        self._execute(
            """
            UPDATE pipeline_runs
            SET finished_at = ?, status = ?, error_summary = ?,
                created_news = ?, created_snapshots = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (
                datetime_to_utc_text(finished_at),
                status,
                _validate_safe_error_summary(error_summary),
                created_news,
                created_snapshots,
                run_id,
            ),
        )
        record = self.get_run(run_id)
        if record is None:
            raise RepositoryError("Pipeline run 更新后无法读取")
        return record

    def get_run(self, run_id: str) -> PipelineRunRecord | None:
        row = self._execute(
            "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return PipelineRunRecord(
            run_id=row["run_id"],
            started_at=utc_text_to_datetime(row["started_at"]),
            finished_at=(
                utc_text_to_datetime(row["finished_at"])
                if row["finished_at"] is not None
                else None
            ),
            status=row["status"],
            dry_run=bool(row["dry_run"]),
            config_hash=row["config_hash"],
            app_version=row["app_version"],
            error_summary=row["error_summary"],
            created_news=row["created_news"],
            created_snapshots=row["created_snapshots"],
        )


@dataclass(frozen=True, slots=True)
class ProviderCallRecord:
    id: int
    run_id: str
    security_id: int | None
    provider: str
    operation: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    status: str
    item_count: int
    retry_count: int
    error_severity: str | None
    error_category: str | None
    error_code: str | None
    error_message: str | None
    request_fingerprint: str | None


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_ISSUE_SEVERITIES = {"error", "warning", "info"}
_ISSUE_CATEGORIES = {
    "network",
    "parse",
    "auth",
    "missing_data",
    "rate_limit",
    "validation",
    "provider_unavailable",
    "unknown",
}
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|authorization)\s*[:=]"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def _validate_safe_error_summary(value: str | None) -> str | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    if (
        "\n" in normalized
        or "\r" in normalized
        or "traceback" in normalized.lower()
        or _SENSITIVE_ASSIGNMENT_RE.search(normalized)
        or _BEARER_RE.search(normalized)
        or _OPENAI_KEY_RE.search(normalized)
        or re.search(r"https?://\S+[?&]\S+", normalized)
    ):
        raise ValueError("error_message 必须是无堆栈、无凭据、无查询参数的安全摘要")
    return normalized[:500]


class ProviderCallRepository(_Repository):
    _FINAL_STATUSES = {"success", "empty", "failed", "skipped"}

    def start_call(
        self,
        run_id: str,
        provider: str,
        operation: str,
        started_at: datetime,
        *,
        security_id: int | None = None,
        request_fingerprint: str | None = None,
    ) -> int:
        provider = provider.strip()
        operation = operation.strip()
        if not provider or not operation:
            raise ValueError("provider 和 operation 不能为空")
        if request_fingerprint is not None and not _SHA256_RE.fullmatch(
            request_fingerprint
        ):
            raise ValueError("request_fingerprint 必须是 SHA-256 十六进制摘要")
        cursor = self._execute(
            """
            INSERT INTO provider_calls(
                run_id, security_id, provider, operation, started_at,
                status, request_fingerprint
            ) VALUES (?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id,
                security_id,
                provider,
                operation,
                datetime_to_utc_text(started_at),
                request_fingerprint,
            ),
        )
        return int(cursor.lastrowid)

    def finish_call(
        self,
        call_id: int,
        status: str,
        finished_at: datetime,
        *,
        duration_ms: int | None = None,
        item_count: int = 0,
        retry_count: int = 0,
        error_severity: str | None = None,
        error_category: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ProviderCallRecord:
        if status not in self._FINAL_STATUSES:
            raise ValueError("Provider call 状态无效")
        if duration_ms is not None and duration_ms < 0:
            raise ValueError("duration_ms 不得为负数")
        if item_count < 0 or retry_count < 0:
            raise ValueError("item_count 和 retry_count 不得为负数")
        if error_code is not None and not _SAFE_CODE_RE.fullmatch(error_code):
            raise ValueError("error_code 只能是安全的短标识")
        if error_severity is not None and error_severity not in _ISSUE_SEVERITIES:
            raise ValueError("error_severity 不符合 DataIssue 枚举")
        if error_category is not None and error_category not in _ISSUE_CATEGORIES:
            raise ValueError("error_category 不符合 DataIssue 枚举")
        existing = self.get_call(call_id)
        if existing is None:
            raise RepositoryError(f"Provider call 不存在: {call_id}")
        if existing.status != "running":
            raise RepositoryError(f"Provider call 已结束: {call_id}")
        self._execute(
            """
            UPDATE provider_calls
            SET finished_at = ?, duration_ms = ?, status = ?, item_count = ?,
                retry_count = ?, error_severity = ?, error_category = ?,
                error_code = ?, error_message = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                datetime_to_utc_text(finished_at),
                duration_ms,
                status,
                item_count,
                retry_count,
                _optional_text(error_severity),
                _optional_text(error_category),
                _optional_text(error_code),
                _validate_safe_error_summary(error_message),
                call_id,
            ),
        )
        record = self.get_call(call_id)
        if record is None:
            raise RepositoryError("Provider call 更新后无法读取")
        return record

    def get_call(self, call_id: int) -> ProviderCallRecord | None:
        row = self._execute(
            "SELECT * FROM provider_calls WHERE id = ?", (call_id,)
        ).fetchone()
        if row is None:
            return None
        return ProviderCallRecord(
            id=row["id"],
            run_id=row["run_id"],
            security_id=row["security_id"],
            provider=row["provider"],
            operation=row["operation"],
            started_at=utc_text_to_datetime(row["started_at"]),
            finished_at=(
                utc_text_to_datetime(row["finished_at"])
                if row["finished_at"] is not None
                else None
            ),
            duration_ms=row["duration_ms"],
            status=row["status"],
            item_count=row["item_count"],
            retry_count=row["retry_count"],
            error_severity=row["error_severity"],
            error_category=row["error_category"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            request_fingerprint=row["request_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class RawResponseRecord:
    id: int
    provider_call_id: int
    sequence: int
    fetched_at: datetime
    http_status: int | None
    content_type: str | None
    body: bytes | None
    body_sha256: str
    compression: str | None
    is_redacted: bool


class RawResponseRepository(_Repository):
    def insert_redacted_response(
        self,
        provider_call_id: int,
        sequence: int,
        fetched_at: datetime,
        body: bytes | str | None,
        *,
        http_status: int | None = None,
        content_type: str | None = None,
        compression: str | None = None,
        is_redacted: bool = True,
    ) -> int:
        if not is_redacted:
            raise ValueError("本阶段拒绝保存未脱敏响应")
        if isinstance(body, Mapping):
            raise TypeError("不得直接保存映射响应；请先显式脱敏并序列化")
        if sequence < 0:
            raise ValueError("sequence 不得为负数")
        if body is None:
            body_bytes = b""
            stored_body = None
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
            stored_body = body_bytes
        elif isinstance(body, bytes):
            body_bytes = body
            stored_body = body
        else:
            raise TypeError("body 只接受已脱敏的 bytes、str 或 None")
        cursor = self._execute(
            """
            INSERT INTO raw_responses(
                provider_call_id, sequence, fetched_at, http_status,
                content_type, body, body_sha256, compression, is_redacted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                provider_call_id,
                sequence,
                datetime_to_utc_text(fetched_at),
                http_status,
                _optional_text(content_type),
                stored_body,
                sha256_bytes(body_bytes),
                _optional_text(compression),
            ),
        )
        return int(cursor.lastrowid)

    def get_by_id(self, response_id: int) -> RawResponseRecord | None:
        row = self._execute(
            "SELECT * FROM raw_responses WHERE id = ?", (response_id,)
        ).fetchone()
        if row is None:
            return None
        return RawResponseRecord(
            id=row["id"],
            provider_call_id=row["provider_call_id"],
            sequence=row["sequence"],
            fetched_at=utc_text_to_datetime(row["fetched_at"]),
            http_status=row["http_status"],
            content_type=row["content_type"],
            body=row["body"],
            body_sha256=row["body_sha256"],
            compression=row["compression"],
            is_redacted=bool(row["is_redacted"]),
        )
