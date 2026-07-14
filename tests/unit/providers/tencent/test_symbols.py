from __future__ import annotations

import pytest

from daily_report_agent.models.security import Security
from daily_report_agent.providers.errors import ProviderValidationError
from daily_report_agent.providers.tencent.symbols import (
    build_tencent_symbol_list,
    to_tencent_symbol,
)


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("600000", "sh600000"),
        ("601318", "sh601318"),
        ("688981", "sh688981"),
        ("000001", "sz000001"),
        ("002594", "sz002594"),
        ("300750", "sz300750"),
    ],
)
def test_maps_supported_a_share_symbols(symbol: str, expected: str) -> None:
    assert to_tencent_symbol(Security("cn", symbol, "Test")) == expected


@pytest.mark.parametrize(
    ("security", "code"),
    [
        (Security("us", "AAPL", "Apple"), "unsupported_market"),
        (Security("cn", "ABC123", "Invalid"), "invalid_symbol"),
        (Security("cn", "60000", "Invalid"), "invalid_symbol"),
        (Security("cn", "８０００００", "Invalid"), "invalid_symbol"),
        (Security("cn", "430047", "BSE"), "unsupported_exchange"),
        (Security("cn", "830799", "BSE"), "unsupported_exchange"),
    ],
)
def test_rejects_ambiguous_or_unsupported_symbols(
    security: Security,
    code: str,
) -> None:
    with pytest.raises(ProviderValidationError) as caught:
        to_tencent_symbol(security)
    assert caught.value.provider_id == "tencent-finance"
    assert caught.value.code == code


def test_batch_mapping_preserves_order_and_does_not_mutate_security() -> None:
    securities = (
        Security("cn", "300750", "宁德时代"),
        Security("cn", "600000", "浦发银行"),
    )
    before = tuple((item.market, item.symbol, item.name) for item in securities)

    result = build_tencent_symbol_list(securities)

    assert result == ("sz300750", "sh600000")
    assert tuple((item.market, item.symbol, item.name) for item in securities) == before


def test_batch_mapping_requires_tuple_and_rejects_duplicates() -> None:
    security = Security("cn", "600000", "浦发银行")
    with pytest.raises(ProviderValidationError, match="tuple"):
        build_tencent_symbol_list([security])  # type: ignore[arg-type]
    with pytest.raises(ProviderValidationError) as caught:
        build_tencent_symbol_list((security, security))
    assert caught.value.code == "duplicate_symbol"
