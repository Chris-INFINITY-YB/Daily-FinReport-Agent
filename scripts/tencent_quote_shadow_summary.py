"""只读汇总腾讯行情 Shadow 的 SQLite 观测记录。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _days(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--days 必须是正整数") from exc
    if not 1 <= parsed <= 365:
        raise argparse.ArgumentTypeError("--days 必须在 1 到 365 之间")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tencent quote shadow read-only summary")
    parser.add_argument("--database", required=True)
    parser.add_argument("--days", type=_days, default=7)
    return parser


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _summary(connection: sqlite3.Connection, *, days: int, now: datetime) -> list[str]:
    since = now - timedelta(days=days)
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS calls,
            SUM(CASE WHEN pc.status = 'success' THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN pc.status = 'empty' THEN 1 ELSE 0 END) AS empties,
            SUM(CASE WHEN pc.status = 'failed' THEN 1 ELSE 0 END) AS failures,
            AVG(pc.duration_ms) AS average_duration,
            MAX(pc.duration_ms) AS maximum_duration,
            COALESCE(SUM(pc.item_count), 0) AS total_items,
            MAX(pc.started_at) AS latest_call
        FROM provider_calls AS pc
        JOIN pipeline_runs AS pr ON pr.run_id = pc.run_id
        WHERE pc.provider = 'tencent-finance'
          AND pc.operation = 'quote_shadow'
          AND pc.started_at >= ?
        """,
        (_utc_text(since),),
    ).fetchone()
    calls = int(row["calls"] or 0)
    successes = int(row["successes"] or 0)
    success_rate = successes / calls * 100 if calls else 0.0
    snapshot_count = connection.execute(
        "SELECT COUNT(*) FROM market_snapshots WHERE source = 'tencent-finance'"
    ).fetchone()[0]
    latest_rows = connection.execute(
        """
        SELECT s.symbol, MAX(ms.observed_at) AS latest_observed_at
        FROM market_snapshots AS ms
        JOIN securities AS s ON s.id = ms.security_id
        WHERE ms.source = 'tencent-finance'
        GROUP BY s.market, s.symbol
        ORDER BY s.symbol
        """
    ).fetchall()

    lines = [
        f"window_start: {_utc_text(since)}",
        f"window_end: {_utc_text(now)}",
        f"logical_calls: {calls}",
        f"success: {successes}",
        f"empty: {int(row['empties'] or 0)}",
        f"failed: {int(row['failures'] or 0)}",
        f"success_rate_pct: {success_rate:.2f}",
        "average_duration_ms: "
        + ("unavailable" if row["average_duration"] is None else f"{row['average_duration']:.2f}"),
        "maximum_duration_ms: "
        + ("unavailable" if row["maximum_duration"] is None else str(row["maximum_duration"])),
        f"total_item_count: {int(row['total_items'])}",
        f"latest_call: {row['latest_call'] or 'none'}",
        f"tencent_snapshot_rows: {int(snapshot_count)}",
    ]
    for latest in latest_rows:
        observed = datetime.fromisoformat(latest["latest_observed_at"].replace("Z", "+00:00"))
        freshness_hours = max(0.0, (now - observed.astimezone(timezone.utc)).total_seconds() / 3600)
        lines.append(
            f"symbol_latest: {latest['symbol']} observed_at={latest['latest_observed_at']} "
            f"freshness_hours={freshness_hours:.2f}"
        )
    lines.extend(
        [
            "missing_symbols: 当前 schema 无法精确统计",
            "idempotency: security_id + source + observed_at 唯一键避免重复快照",
        ]
    )
    return lines


def main(
    argv: list[str] | None = None,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> int:
    args = _parser().parse_args(argv)
    try:
        with closing(_connect_read_only(Path(args.database))) as connection:
            lines = _summary(
                connection,
                days=args.days,
                now=_clock_value(clock),
            )
    except FileNotFoundError:
        print("summary failed: database does not exist", file=sys.stderr)
        return 2
    except (sqlite3.Error, ValueError):
        print("summary failed: database could not be read safely", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
