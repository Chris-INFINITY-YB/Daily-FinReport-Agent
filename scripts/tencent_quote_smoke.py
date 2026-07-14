"""显式启用的腾讯 A 股行情最小在线冒烟入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import ProviderError
from daily_report_agent.providers.tencent.online_transport import (
    TencentOnlineQuoteTransport,
)
from daily_report_agent.providers.tencent.quote import TencentQuoteProvider


DEFAULT_SYMBOLS = "600519,000001,300750"
MAX_SMOKE_SYMBOLS = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tencent quote controlled online smoke")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser


def _parse_symbols(value: str) -> tuple[Security, ...]:
    symbols = tuple(part.strip() for part in value.split(",") if part.strip())
    if not 1 <= len(symbols) <= MAX_SMOKE_SYMBOLS:
        raise ValueError("smoke requires between one and three explicit symbols")
    if len(symbols) != len(set(symbols)):
        raise ValueError("smoke symbols must not be duplicated")
    return tuple(Security(market="cn", symbol=symbol, name=symbol) for symbol in symbols)


def _print_snapshot(item) -> None:
    print()
    print(item.symbol)
    print(f"observed_at: {item.observed_at.isoformat()}")
    print(f"price_present: {str(item.price is not None).lower()}")
    print(f"previous_close_present: {str(item.previous_close is not None).lower()}")
    print(f"pct_change_present: {str(item.pct_change is not None).lower()}")
    consistent = None
    if (
        item.price is not None
        and item.previous_close not in {None, 0}
        and item.pct_change is not None
    ):
        calculated = (item.price - item.previous_close) / item.previous_close * 100
        consistent = abs(calculated - item.pct_change) <= 0.02
    print(
        "pct_cross_check: "
        + ("unavailable" if consistent is None else str(consistent).lower())
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_network:
        print("network disabled: pass --allow-network explicitly", file=sys.stderr)
        return 2
    try:
        securities = _parse_symbols(args.symbols)
        transport = TencentOnlineQuoteTransport()
        provider = TencentQuoteProvider(
            transport,
            timeout_seconds=args.timeout,
            batch_size=MAX_SMOKE_SYMBOLS,
        )
        result = provider.fetch_quotes(securities)
    except (ValueError, ProviderError) as exc:
        if isinstance(exc, ProviderError):
            print(
                f"smoke failed: {type(exc).__name__} "
                f"code={exc.code or 'none'} retryable={str(exc.retryable).lower()}",
                file=sys.stderr,
            )
        else:
            print(f"smoke rejected: {exc}", file=sys.stderr)
        return 1

    metadata = transport.last_response_metadata
    print("provider: tencent-finance")
    print(f"requested: {len(securities)}")
    print(f"received: {len(result.items)}")
    print(f"issues: {len(result.issues)}")
    print(f"requests: {transport.request_count}")
    if metadata is not None:
        print(f"http_status: {metadata.http_status}")
        print(f"content_type: {metadata.content_type or 'missing'}")
        print(f"encoding: {metadata.encoding}")
        print(f"response_bytes: {metadata.response_bytes}")
        print(f"response_order: {','.join(metadata.record_symbols) or 'none'}")
        print(f"record_statuses: {','.join(metadata.record_statuses) or 'none'}")
        print(
            "field_counts: "
            + (",".join(str(value) for value in metadata.field_counts) or "none")
        )
    for item in result.items:
        _print_snapshot(item)
    for issue in result.issues:
        print(
            f"issue: severity={issue.severity.value} category={issue.category.value} "
            f"code={issue.code or 'none'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
