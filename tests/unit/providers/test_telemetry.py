from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError, fields

import pytest

from daily_report_agent.providers.telemetry import (
    ProviderMetricEvent,
    ProviderMetricStatus,
    emit_provider_metric_safely,
    format_provider_metric_event,
    log_provider_metric,
)


def _event(**overrides) -> ProviderMetricEvent:
    values = {
        "provider_id": "tencent-finance",
        "operation": "quote_shadow",
        "status": ProviderMetricStatus.SUCCESS,
        "duration_ms": 125,
        "item_count": 2,
        "issue_count": 0,
        "retry_count": 0,
        "error_code": None,
    }
    values.update(overrides)
    return ProviderMetricEvent(**values)


def test_success_event_has_exact_public_field_whitelist_and_is_immutable() -> None:
    event = _event()
    assert tuple(field.name for field in fields(event)) == (
        "provider_id",
        "operation",
        "status",
        "duration_ms",
        "item_count",
        "issue_count",
        "retry_count",
        "error_code",
    )
    assert not hasattr(event, "__dict__")
    with pytest.raises(FrozenInstanceError):
        event.item_count = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    "status",
    [
        ProviderMetricStatus.EMPTY,
        ProviderMetricStatus.PARTIAL,
        ProviderMetricStatus.FAILED,
        ProviderMetricStatus.SKIPPED,
    ],
)
def test_terminal_statuses_are_closed_enum_values(status: ProviderMetricStatus) -> None:
    assert _event(status=status).status is status


def test_arbitrary_status_string_is_rejected() -> None:
    with pytest.raises(TypeError, match="ProviderMetricStatus"):
        _event(status="success")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ProviderMetricStatus"):
        _event(status="timed_out")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "Tencent"),
        ("provider_id", "https://provider.invalid"),
        ("operation", "fetch quotes"),
        ("operation", "token=secret"),
        ("operation", "a" * 65),
    ],
)
def test_identifiers_reject_invalid_or_sensitive_shapes(field: str, value: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        _event(**{field: value})


@pytest.mark.parametrize(
    "error_code",
    [
        "",
        "HTTP_403",
        "https://provider.invalid?q=secret",
        "api_key=secret",
        "Bearer-secret",
        "response.body",
        "a" * 65,
    ],
)
def test_error_code_rejects_free_text_urls_credentials_and_long_values(
    error_code: str,
) -> None:
    with pytest.raises(ValueError):
        _event(status=ProviderMetricStatus.FAILED, error_code=error_code)


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("duration_ms", -1, ValueError),
        ("item_count", -1, ValueError),
        ("issue_count", -1, ValueError),
        ("retry_count", -1, ValueError),
        ("duration_ms", True, TypeError),
        ("item_count", False, TypeError),
        ("issue_count", True, TypeError),
        ("retry_count", False, TypeError),
    ],
)
def test_duration_and_counts_require_non_negative_non_bool_integers(
    field: str,
    value: int,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        _event(**{field: value})


def test_fixed_json_log_has_deterministic_field_order_and_no_sensitive_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = _event(
        status=ProviderMetricStatus.FAILED,
        issue_count=1,
        error_code="network_error",
    )
    expected = (
        '{"provider_id":"tencent-finance","operation":"quote_shadow",'
        '"status":"failed","duration_ms":125,"item_count":2,'
        '"issue_count":1,"retry_count":0,"error_code":"network_error"}'
    )
    assert format_provider_metric_event(event) == expected

    with caplog.at_level(logging.INFO, logger="daily_report_agent.provider_metrics"):
        log_provider_metric(event)
    assert [record.getMessage() for record in caplog.records] == [expected]
    assert tuple(json.loads(expected)) == tuple(field.name for field in fields(event))
    forbidden_values = (
        "600519",
        "贵州茅台",
        "https://provider.invalid?q=secret",
        "private-token",
        "Cookie:",
        "Authorization:",
        "response body",
        "exception text",
        "/private/agent.sqlite",
    )
    assert all(value not in expected for value in forbidden_values)


def test_formatter_rejects_unvalidated_lookalike_objects() -> None:
    class Lookalike:
        provider_id = "tencent-finance"
        operation = "quote_shadow"
        details = {"token": "secret"}

    with pytest.raises(TypeError, match="ProviderMetricEvent"):
        format_provider_metric_event(Lookalike())  # type: ignore[arg-type]


def test_normal_emitter_failure_is_isolated() -> None:
    calls = []

    def fail(event: ProviderMetricEvent) -> None:
        calls.append(event)
        raise RuntimeError(
            "https://provider.invalid response body token=secret symbol=600519"
        )

    event = _event()
    assert emit_provider_metric_safely(event, fail) is None
    assert calls == [event]


@pytest.mark.parametrize("control_flow", [KeyboardInterrupt(), SystemExit(3)])
def test_control_flow_exceptions_are_not_silently_swallowed(
    control_flow: BaseException,
) -> None:
    def stop(event: ProviderMetricEvent) -> None:
        raise control_flow

    with pytest.raises(type(control_flow)):
        emit_provider_metric_safely(_event(), stop)
