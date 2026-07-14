"""标准 A 股代码到腾讯行情代码的纯映射。"""

from __future__ import annotations

from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import ProviderValidationError

from .constants import TENCENT_QUOTE_DESCRIPTOR


_SHANGHAI_PREFIXES = ("600", "601", "603", "605", "688")
_SHENZHEN_PREFIXES = ("000", "001", "002", "003", "300", "301")


def _validation_error(message: str, code: str) -> ProviderValidationError:
    return ProviderValidationError(
        provider_id=TENCENT_QUOTE_DESCRIPTOR.provider_id,
        operation="map_symbol",
        safe_message=message,
        code=code,
    )


def to_tencent_symbol(security: Security) -> str:
    """映射沪深主要 A 股；北交所和未确认号段会被显式拒绝。"""
    if not isinstance(security, Security):
        raise _validation_error("Security input is invalid", "invalid_security")
    if security.market != "cn":
        raise _validation_error(
            "Tencent quote provider only supports the cn market",
            "unsupported_market",
        )
    symbol = security.symbol.strip()
    if len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
        raise _validation_error(
            "A-share symbol must contain exactly six ASCII digits",
            "invalid_symbol",
        )
    if symbol.startswith(_SHANGHAI_PREFIXES):
        return f"sh{symbol}"
    if symbol.startswith(_SHENZHEN_PREFIXES):
        return f"sz{symbol}"
    raise _validation_error(
        "A-share exchange prefix is not supported",
        "unsupported_exchange",
    )


def build_tencent_symbol_list(
    securities: tuple[Security, ...],
) -> tuple[str, ...]:
    """按输入顺序批量映射，并拒绝会造成响应关联歧义的重复代码。"""
    if not isinstance(securities, tuple):
        raise _validation_error("securities must be a tuple", "invalid_collection")
    symbols = tuple(to_tencent_symbol(security) for security in securities)
    if len(symbols) != len(set(symbols)):
        raise _validation_error(
            "Duplicate securities are not supported",
            "duplicate_symbol",
        )
    return symbols
