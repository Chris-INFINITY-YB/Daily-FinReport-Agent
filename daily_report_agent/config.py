"""与运行逻辑分离的严格配置模型。"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TencentQuoteShadowSettings:
    enabled: bool = False
    timeout_seconds: float = 10.0
    batch_size: int = 20
    max_symbols: int = 20


_TENCENT_QUOTE_KEYS = frozenset(
    {"shadow_enabled", "timeout_seconds", "batch_size", "max_symbols"}
)


def _positive_int(value: object, *, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    if value > maximum:
        raise ValueError(f"{name} 不得超过 {maximum}")
    return value


def parse_tencent_quote_shadow_settings(
    config: dict,
) -> TencentQuoteShadowSettings:
    """解析默认关闭的腾讯 Shadow 配置，不读取文件或环境变量。"""
    if not isinstance(config, dict):
        raise TypeError("config 必须是字典")
    providers = config.get("providers")
    if providers is None:
        return TencentQuoteShadowSettings()
    if not isinstance(providers, dict):
        raise ValueError("providers 配置必须是 YAML 映射")
    raw = providers.get("tencent_quote")
    if raw is None:
        return TencentQuoteShadowSettings()
    if not isinstance(raw, dict):
        raise ValueError("providers.tencent_quote 必须是 YAML 映射")
    unsupported = set(raw) - _TENCENT_QUOTE_KEYS
    if unsupported:
        raise ValueError("providers.tencent_quote 包含不支持的配置项")

    enabled = raw.get("shadow_enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("providers.tencent_quote.shadow_enabled 必须是布尔值")

    timeout = raw.get("timeout_seconds", 10.0)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("providers.tencent_quote.timeout_seconds 必须是正有限数值")
    if timeout > 60:
        raise ValueError("providers.tencent_quote.timeout_seconds 不得超过 60")

    return TencentQuoteShadowSettings(
        enabled=enabled,
        timeout_seconds=float(timeout),
        batch_size=_positive_int(
            raw.get("batch_size", 20),
            name="providers.tencent_quote.batch_size",
            maximum=100,
        ),
        max_symbols=_positive_int(
            raw.get("max_symbols", 20),
            name="providers.tencent_quote.max_symbols",
            maximum=100,
        ),
    )
