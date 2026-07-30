"""与运行逻辑分离的严格配置模型。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from daily_report_agent.providers.contracts import normalize_provider_id
from daily_report_agent.providers.routing import DataRouteMode


@dataclass(frozen=True, slots=True)
class ProviderRoutingSettings:
    """正式路由配置；默认模式为 legacy。"""

    mode: DataRouteMode = DataRouteMode.LEGACY
    allow_legacy_fallback: bool = True
    max_call_budget: int = 1
    provider_priorities: tuple[tuple[str, int], ...] = ()
    provider_shadow_database_path: str | None = None

    @property
    def candidate_provider_ids(self) -> tuple[str, ...]:
        return tuple(provider_id for provider_id, _ in self.provider_priorities)


@dataclass(frozen=True, slots=True)
class TencentQuoteShadowSettings:
    enabled: bool = False
    timeout_seconds: float = 10.0
    batch_size: int = 20
    max_symbols: int = 20


_TENCENT_QUOTE_KEYS = frozenset(
    {"shadow_enabled", "timeout_seconds", "batch_size", "max_symbols"}
)
_PIPELINE_KEYS = frozenset(
    {
        "data_route",
        "fallback_to_legacy",
        "max_provider_calls",
        "provider_priorities",
        "provider_shadow_database_path",
    }
)
_MAX_PROVIDER_PRIORITY = 1_000_000
_MAX_PROVIDER_CALLS = 100


def _positive_int(value: object, *, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    if value > maximum:
        raise ValueError(f"{name} 不得超过 {maximum}")
    return value


def _bounded_non_negative_int(
    value: object,
    *,
    name: str,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} 必须是非负整数")
    if value > maximum:
        raise ValueError(f"{name} 不得超过 {maximum}")
    return value


def parse_provider_routing_settings(config: dict) -> ProviderRoutingSettings:
    """严格解析纯离线路由设置，不读取文件、环境变量、凭据或 Transport。"""
    if not isinstance(config, dict):
        raise TypeError("config 必须是字典")
    raw = config.get("pipeline")
    if raw is None:
        return ProviderRoutingSettings()
    if not isinstance(raw, dict):
        raise ValueError("pipeline 配置必须是 YAML 映射")
    unsupported = set(raw) - _PIPELINE_KEYS
    if unsupported:
        raise ValueError("pipeline 包含不支持的配置项")

    mode_value = raw.get("data_route", DataRouteMode.LEGACY.value)
    if not isinstance(mode_value, str):
        raise ValueError("pipeline.data_route 必须是字符串")
    try:
        mode = DataRouteMode(mode_value)
    except ValueError as exc:
        raise ValueError(
            "pipeline.data_route 只允许 legacy、provider_shadow 或 provider_primary"
        ) from exc

    fallback = raw.get("fallback_to_legacy", True)
    if not isinstance(fallback, bool):
        raise ValueError("pipeline.fallback_to_legacy 必须是布尔值")
    max_calls = _bounded_non_negative_int(
        raw.get("max_provider_calls", 1),
        name="pipeline.max_provider_calls",
        maximum=_MAX_PROVIDER_CALLS,
    )

    priorities_value = raw.get("provider_priorities", {})
    if not isinstance(priorities_value, dict):
        raise ValueError("pipeline.provider_priorities 必须是 YAML 映射")
    priorities: list[tuple[str, int]] = []
    for provider_id, priority_value in priorities_value.items():
        try:
            normalized_id = normalize_provider_id(provider_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "pipeline.provider_priorities 包含非法 Provider ID"
            ) from exc
        priority = _bounded_non_negative_int(
            priority_value,
            name=f"pipeline.provider_priorities.{normalized_id}",
            maximum=_MAX_PROVIDER_PRIORITY,
        )
        priorities.append((normalized_id, priority))
    priorities.sort(key=lambda item: (item[1], item[0]))

    shadow_database_path = raw.get("provider_shadow_database_path")
    if shadow_database_path is not None:
        if not isinstance(shadow_database_path, str):
            raise ValueError(
                "pipeline.provider_shadow_database_path 必须是非空字符串或 null"
            )
        shadow_database_path = shadow_database_path.strip()
        if not shadow_database_path:
            raise ValueError(
                "pipeline.provider_shadow_database_path 必须是非空字符串或 null"
            )

    return ProviderRoutingSettings(
        mode=mode,
        allow_legacy_fallback=fallback,
        max_call_budget=max_calls,
        provider_priorities=tuple(priorities),
        provider_shadow_database_path=shadow_database_path,
    )


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
