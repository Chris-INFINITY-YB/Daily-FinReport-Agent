from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_package_can_be_imported() -> None:
    import daily_report_agent

    assert daily_report_agent.__name__ == "daily_report_agent"


def test_eastmoney_provider_is_packaged_and_importable() -> None:
    project_config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"daily_report_agent.providers.eastmoney"' in project_config

    from daily_report_agent.providers.eastmoney import (
        EASTMONEY_NEWS_DESCRIPTOR,
        EASTMONEY_PROFILE_DESCRIPTOR,
        EastmoneyNewsProvider,
        EastmoneyProfileProvider,
    )

    assert EASTMONEY_NEWS_DESCRIPTOR.provider_id == "eastmoney"
    assert EastmoneyNewsProvider.descriptor is EASTMONEY_NEWS_DESCRIPTOR
    assert EASTMONEY_PROFILE_DESCRIPTOR.provider_id == "eastmoney"
    assert EastmoneyProfileProvider.descriptor is EASTMONEY_PROFILE_DESCRIPTOR


def test_cninfo_provider_is_packaged_and_importable() -> None:
    project_config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"daily_report_agent.providers.cninfo"' in project_config

    from daily_report_agent.providers import (
        CNINFO_PROFILE_DESCRIPTOR as TOP_LEVEL_DESCRIPTOR,
        CninfoProfileProvider as TopLevelProvider,
    )
    from daily_report_agent.providers.cninfo import (
        CNINFO_PROFILE_DESCRIPTOR,
        CninfoProfileProvider,
    )

    assert CNINFO_PROFILE_DESCRIPTOR.provider_id == "cninfo"
    assert CninfoProfileProvider.descriptor is CNINFO_PROFILE_DESCRIPTOR
    assert TOP_LEVEL_DESCRIPTOR is CNINFO_PROFILE_DESCRIPTOR
    assert TopLevelProvider is CninfoProfileProvider
