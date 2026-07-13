"""标准证券身份模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Security:
    market: str
    symbol: str
    name: str
    exchange: str | None = None
    currency: str | None = None
    industry: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        market = self.market.strip().lower()
        symbol = self.symbol.strip()
        name = self.name.strip()

        if market not in {"cn", "us"}:
            raise ValueError("market 只允许 'cn' 或 'us'")
        if not symbol:
            raise ValueError("symbol 不能为空")
        if not name:
            raise ValueError("name 不能为空")
        if not isinstance(self.aliases, tuple):
            raise TypeError("aliases 必须是 tuple[str, ...]")
        if any(not isinstance(alias, str) or not alias.strip() for alias in self.aliases):
            raise ValueError("aliases 不能包含空值或非字符串")

        object.__setattr__(self, "market", market)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "name", name)
