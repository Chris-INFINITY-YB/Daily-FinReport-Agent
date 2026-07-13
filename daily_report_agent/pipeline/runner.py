"""将标准分析输入旁路保存到 SQLite，不被业务模块反向依赖。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from daily_report_agent.models.analysis import AnalysisInput

from .context import RunContext


def _warn(operation: str, exc: Exception) -> None:
    print(f"[存储警告] {operation}失败，日报将继续: {type(exc).__name__}")


def _storage_settings(config: dict) -> tuple[bool, str]:
    settings = config.get("storage")
    if settings is None:
        return False, "data/agent.db"
    if not isinstance(settings, dict):
        raise ValueError("storage 配置必须是 YAML 映射")
    enabled = settings.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("storage.enabled 必须是布尔值")
    path = settings.get("path", "data/agent.db")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("storage.path 必须是非空字符串")
    return enabled, path.strip()


def _resolve_database_path(config_path: str, configured_path: str) -> Path:
    path = Path(configured_path).expanduser()
    if path.is_absolute():
        return path
    return Path(config_path).resolve().parent / path


def start_run_context(
    config: dict,
    *,
    config_path: str,
    dry_run: bool,
) -> RunContext:
    """按配置启动 pipeline_run；dry-run 强制禁用且不会导入 storage。"""
    started_at = datetime.now(timezone.utc)
    context = RunContext(
        run_id=str(uuid4()),
        started_at=started_at,
        storage_enabled=False,
    )
    if dry_run:
        return context

    enabled, configured_path = _storage_settings(config)
    if not enabled:
        return context
    context.storage_enabled = True

    try:
        # 延迟导入保证默认关闭和 dry-run 不加载 SQLite 存储实现。
        from daily_report_agent.storage.database import Database
        from daily_report_agent.storage.repositories import PipelineRunRepository

        database_path = _resolve_database_path(config_path, configured_path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        database = Database(database_path)
        database.initialize()
        config_hash = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
        with database.transaction() as connection:
            PipelineRunRepository(connection).start_run(
                context.run_id,
                context.started_at,
                dry_run=False,
                config_hash=config_hash,
            )
        context.database = database
        context.pipeline_run_id = context.run_id
    except Exception as exc:
        context.storage_failed = True
        _warn("初始化", exc)
    return context


def persist_analysis_input(
    context: RunContext,
    analysis_input: AnalysisInput,
) -> bool:
    """按标的事务保存标准模型；失败降级，不保存 PriceWindow。"""
    if not context.storage_active:
        return not context.storage_failed

    try:
        from daily_report_agent.storage.repositories import (
            MarketSnapshotRepository,
            NewsRepository,
            SecurityRepository,
        )

        created_news = 0
        created_snapshots = 0
        fetched_at = datetime.now(timezone.utc)
        with context.database.transaction() as connection:
            security_repository = SecurityRepository(connection)
            news_repository = NewsRepository(connection)
            snapshot_repository = MarketSnapshotRepository(connection)

            security_id = security_repository.upsert_security(analysis_input.security)
            if analysis_input.security.aliases:
                security_repository.replace_aliases(
                    security_id,
                    analysis_input.security.aliases,
                )

            for news in analysis_input.news:
                news_id, inserted = news_repository.insert_or_get_news(news)
                news_repository.link_to_security(news_id, security_id)
                created_news += int(inserted)

            for snapshot in analysis_input.market_snapshots:
                _, inserted = snapshot_repository.insert_or_get_snapshot(
                    security_id,
                    snapshot,
                    fetched_at,
                )
                created_snapshots += int(inserted)

        context.created_news += created_news
        context.created_snapshots += created_snapshots
        if analysis_input.issues:
            print(f"[数据质量] {analysis_input.security.symbol}: {len(analysis_input.issues)} 项")
        return True
    except Exception as exc:
        context.storage_failed = True
        _warn(f"保存 {analysis_input.security.symbol}", exc)
        return False


def finish_storage_run(
    context: RunContext,
    status: str,
    *,
    error_summary: str | None = None,
) -> None:
    """尽力结束 pipeline_run；存储失败不会覆盖日报主流程结果。"""
    if not context.storage_active:
        return
    try:
        from daily_report_agent.storage.repositories import PipelineRunRepository

        with context.database.transaction() as connection:
            PipelineRunRepository(connection).finish_run(
                context.pipeline_run_id,
                status,
                datetime.now(timezone.utc),
                error_summary=error_summary,
                created_news=context.created_news,
                created_snapshots=context.created_snapshots,
            )
    except Exception as exc:
        context.storage_failed = True
        _warn("结束运行记录", exc)
