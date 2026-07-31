"""M1-05B 预留的单次 Tencent ProviderRouter Shadow 受控入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from daily_report_agent.config import parse_provider_routing_settings
from daily_report_agent.models.security import Security
from daily_report_agent.pipeline.tencent_provider_shadow import (
    TencentProviderShadowResult,
    run_tencent_provider_shadow_from_config,
    validate_tencent_provider_shadow_gate,
)
from daily_report_agent.providers.tencent.symbols import to_tencent_symbol


FIXED_SYMBOLS = ("600519", "300750", "000001")


class _HttpRequestCounter:
    def __init__(self) -> None:
        self.count = 0


class _OneRequestTransport:
    """在底层 Transport 外再次强制一次 HTTP 调用上限。"""

    def __init__(self, transport: object, counter: _HttpRequestCounter) -> None:
        self._transport = transport
        self._counter = counter

    def fetch_quote_text(self, symbols, *, timeout_seconds):
        if self._counter.count >= 1:
            raise RuntimeError("M1-05B HTTP request budget exhausted")
        self._counter.count += 1
        return self._transport.fetch_quote_text(
            symbols,
            timeout_seconds=timeout_seconds,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="单次 Tencent ProviderRouter Shadow 受控验证"
    )
    parser.add_argument("--allow-network-once", action="store_true")
    parser.add_argument("--database", required=True)
    parser.add_argument(
        "--symbols",
        default=",".join(FIXED_SYMBOLS),
        help="必须精确为 600519,300750,000001",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser


def _validated_symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(item.strip() for item in value.split(","))
    if symbols != FIXED_SYMBOLS:
        raise ValueError("M1-05B 证券集合或顺序不符合固定门禁")
    for symbol in symbols:
        to_tencent_symbol(Security("cn", symbol, symbol))
    return symbols


def _provider_factory(counter: _HttpRequestCounter):
    def build(settings):
        # 仅在完整 CLI、路径、SQLite 和 Circuit 门禁之后由 Invoker 惰性调用。
        from daily_report_agent.providers.tencent.online_transport import (
            TencentOnlineQuoteTransport,
        )
        from daily_report_agent.providers.tencent.quote import (
            TencentQuoteProvider,
        )

        return TencentQuoteProvider(
            _OneRequestTransport(TencentOnlineQuoteTransport(), counter),
            timeout_seconds=min(settings.timeout_seconds, 10.0),
            batch_size=3,
        )

    return build


def run_once(
    argv: list[str] | None = None,
    *,
    executor: Callable[..., TencentProviderShadowResult] = (
        run_tencent_provider_shadow_from_config
    ),
) -> int:
    args = build_parser().parse_args(argv)
    if not args.allow_network_once:
        print(
            "refused: --allow-network-once is required",
            file=sys.stderr,
        )
        return 2
    try:
        symbols = _validated_symbols(args.symbols)
        if not 0 < args.timeout <= 10:
            raise ValueError("timeout 必须大于 0 且不超过 10 秒")
        database_path = Path(args.database).expanduser().resolve(strict=False)
        if database_path.exists():
            raise ValueError("M1-05B 数据库路径必须尚不存在")
        project_root = Path(__file__).resolve().parents[1]
        if database_path.is_relative_to(project_root):
            raise ValueError("M1-05B 数据库不得位于项目根目录")
    except (TypeError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    config = {
        "pipeline": {
            "data_route": "provider_shadow",
            "fallback_to_legacy": False,
            "max_provider_calls": 1,
            "provider_priorities": {"tencent-finance": 0},
            "provider_shadow_database_path": str(database_path),
        },
        "storage": {"enabled": False, "path": "data/agent.db"},
        "providers": {
            "tencent_quote": {
                "shadow_enabled": True,
                "timeout_seconds": args.timeout,
                "batch_size": 3,
                "max_symbols": 3,
            }
        },
    }
    gate = validate_tencent_provider_shadow_gate(
        config=config,
        config_path=__file__,
        routing=parse_provider_routing_settings(config),
        allow_provider_shadow=True,
        dry_run=False,
    )
    assert gate is not None
    counter = _HttpRequestCounter()
    result = executor(
        gate=gate,
        config_path=__file__,
        watchlist=[
            {"market": "cn", "symbol": symbol, "name": symbol}
            for symbol in symbols
        ],
        provider_factory=_provider_factory(counter),
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "logical_calls": result.call_budget_used,
                "http_requests": counter.count,
                "item_count": result.item_count,
                "issue_count": result.issue_count,
                "retry_count": 0,
                "fallback_count": 0,
                "raw_response_count": 0,
                "error_code": result.safe_error_code,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if result.call_budget_used > 1 or counter.count > 1:
        return 1
    return 0 if result.status.value in {"success", "empty", "partial"} else 1


def main(argv: list[str] | None = None) -> int:
    return run_once(argv)


if __name__ == "__main__":
    raise SystemExit(main())
