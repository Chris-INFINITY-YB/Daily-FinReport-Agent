from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from daily_report_agent.models.issues import (
    DataIssue,
    IssueCategory,
    IssueSeverity,
)
from daily_report_agent.providers.contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderResult,
)


def descriptor(**overrides) -> ProviderDescriptor:
    values = {
        "provider_id": "test-provider_1",
        "display_name": "Test Provider",
        "capabilities": frozenset({ProviderCapability.QUOTE}),
        "markets": frozenset({"cn", "us"}),
    }
    values.update(overrides)
    return ProviderDescriptor(**values)


def test_capability_values_are_stable_strings() -> None:
    assert [(item.name, item.value) for item in ProviderCapability] == [
        ("QUOTE", "quote"),
        ("NEWS", "news"),
        ("PROFILE", "profile"),
    ]
    assert ProviderCapability.QUOTE == "quote"


def test_descriptor_normalizes_boundary_whitespace_and_is_frozen() -> None:
    value = descriptor(provider_id="  test-provider_1  ", display_name=" Test ")
    assert value.provider_id == "test-provider_1"
    assert value.display_name == "Test"
    with pytest.raises(FrozenInstanceError):
        value.provider_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "provider_id",
    ["", "AKShare", "has space", "https://provider.test", "a.b", "供应商"],
)
def test_descriptor_rejects_invalid_provider_ids(provider_id: str) -> None:
    with pytest.raises(ValueError):
        descriptor(provider_id=provider_id)


@pytest.mark.parametrize("markets", [frozenset(), frozenset({"hk"}), frozenset({"CN"})])
def test_descriptor_rejects_invalid_markets(markets: frozenset[str]) -> None:
    with pytest.raises(ValueError):
        descriptor(markets=markets)


def test_descriptor_requires_capability_and_immutable_collections() -> None:
    with pytest.raises(ValueError):
        descriptor(capabilities=frozenset())
    with pytest.raises(TypeError):
        descriptor(capabilities={ProviderCapability.QUOTE})
    with pytest.raises(TypeError):
        descriptor(markets={"cn"})


def test_news_types_require_news_capability() -> None:
    with pytest.raises(ValueError):
        descriptor(supported_news_types=frozenset({"company"}))
    value = descriptor(
        capabilities=frozenset({ProviderCapability.NEWS}),
        supported_news_types=frozenset({" company "}),
    )
    assert value.supported_news_types == frozenset({"company"})


def test_provider_result_uses_immutable_tuples_and_allows_empty_success() -> None:
    empty: ProviderResult[str] = ProviderResult(provider=descriptor())
    assert empty.items == ()
    assert empty.issues == ()
    with pytest.raises(FrozenInstanceError):
        empty.items = ("changed",)  # type: ignore[misc]
    with pytest.raises(TypeError):
        ProviderResult(provider=descriptor(), items=[])
    with pytest.raises(TypeError):
        ProviderResult(provider=descriptor(), issues=[])


def test_provider_result_allows_items_with_quality_issue() -> None:
    issue = DataIssue(
        severity=IssueSeverity.WARNING,
        category=IssueCategory.MISSING_DATA,
        provider="test-provider_1",
        operation="fetch_quotes",
        message="部分字段缺失",
        retryable=False,
        occurred_at=datetime.now(timezone.utc),
    )
    result = ProviderResult(provider=descriptor(), items=("item",), issues=(issue,))
    assert result.items == ("item",)
    assert result.issues == (issue,)


def test_provider_result_has_no_persistence_state() -> None:
    fields = set(ProviderResult.__dataclass_fields__)
    assert fields == {"provider", "items", "issues"}
    assert not hasattr(ProviderResult(provider=descriptor()), "database")
