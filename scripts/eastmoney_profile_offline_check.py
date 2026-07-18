"""Eastmoney Profile Provider 的纯离线合成 Fixture 验收入口。"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import (
    ProviderError,
    ProviderValidationError,
)
from daily_report_agent.providers.eastmoney.profile import (
    EastmoneyProfileProvider,
)
from daily_report_agent.providers.eastmoney.transport import ProfileRows


MAX_FIXTURE_BYTES = 64 * 1024
STANDARD_PROFILE_FIELDS = (
    "symbol",
    "market",
    "name",
    "exchange",
    "currency",
    "industry",
    "description",
    "source",
    "fetched_at",
)


class FixtureInputError(ValueError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class FixtureProfileTransport:
    """Return one immutable in-memory Fixture without any online capability."""

    def __init__(self, rows: ProfileRows) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def fetch_profile_rows(self, symbol: str) -> ProfileRows:
        self.calls.append(symbol)
        return self._rows


@dataclass(frozen=True, slots=True)
class OfflineCheckSummary:
    provider_id: str
    executed: bool
    item_count: int
    issue_codes: tuple[str, ...]
    present_fields: tuple[str, ...]

    def lines(self) -> tuple[str, ...]:
        return (
            "fixture_kind: synthetic",
            f"provider_id: {self.provider_id}",
            f"executed: {str(self.executed).lower()}",
            f"item_count: {self.item_count}",
            f"issue_count: {len(self.issue_codes)}",
            "issue_codes: " + (",".join(self.issue_codes) or "none"),
            "present_fields: " + (",".join(self.present_fields) or "none"),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an always-offline Eastmoney profile synthetic Fixture check"
    )
    parser.add_argument("--fixture", required=True, help="local synthetic JSON file")
    parser.add_argument("--symbol", required=True, help="six-digit CN security symbol")
    parser.add_argument("--name", help="optional display name; defaults to symbol")
    return parser


def _fixture_path(value: str) -> Path:
    normalized = value.strip()
    if not normalized or normalized == "-" or urlsplit(normalized).scheme:
        raise FixtureInputError("fixture_location_rejected")
    path = Path(normalized).expanduser()
    try:
        metadata = path.stat()
    except FileNotFoundError as exc:
        raise FixtureInputError("fixture_missing") from exc
    except OSError as exc:
        raise FixtureInputError("fixture_unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise FixtureInputError("fixture_not_regular_file")
    if metadata.st_size > MAX_FIXTURE_BYTES:
        raise FixtureInputError("fixture_too_large")
    return path


def load_fixture(value: str) -> ProfileRows:
    path = _fixture_path(value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise FixtureInputError("fixture_unreadable") from exc
    except json.JSONDecodeError as exc:
        raise FixtureInputError("fixture_invalid_json") from exc
    if not isinstance(payload, list):
        raise FixtureInputError("fixture_top_level_not_list")

    rows = []
    for row in payload:
        if not isinstance(row, dict):
            raise FixtureInputError("fixture_row_not_object")
        rows.append(MappingProxyType(dict(row)))
    return tuple(rows)


def run_offline_check(rows: ProfileRows, security: Security) -> OfflineCheckSummary:
    transport = FixtureProfileTransport(rows)
    provider = EastmoneyProfileProvider(transport)
    result = provider.fetch_profile(security)
    executed = transport.calls == [security.symbol]
    if not executed:
        raise RuntimeError("offline transport call invariant failed")

    present_fields = tuple(
        field_name
        for field_name in STANDARD_PROFILE_FIELDS
        if any(getattr(item, field_name) is not None for item in result.items)
    )
    issue_codes = tuple(
        issue.code or issue.category.value for issue in result.issues
    )
    return OfflineCheckSummary(
        provider_id=result.provider.provider_id,
        executed=executed,
        item_count=len(result.items),
        issue_codes=issue_codes,
        present_fields=present_fields,
    )


def _print_error(category: str, *, code: str | None = None) -> None:
    suffix = "" if code is None else f" code={code}"
    print(f"offline_check_error: category={category}{suffix}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rows = load_fixture(args.fixture)
        display_name = args.symbol if args.name is None else args.name
        security = Security(market="cn", symbol=args.symbol, name=display_name)
        summary = run_offline_check(rows, security)
    except FixtureInputError as exc:
        _print_error(exc.category)
        return 2
    except (TypeError, ValueError):
        _print_error("invalid_security")
        return 2
    except ProviderValidationError as exc:
        _print_error("invalid_security", code=exc.code)
        return 2
    except ProviderError as exc:
        _print_error("provider_failure", code=exc.code)
        return 1
    except Exception:
        _print_error("internal_failure")
        return 1

    print("\n".join(summary.lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
