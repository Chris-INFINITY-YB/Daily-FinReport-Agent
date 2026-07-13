def test_package_can_be_imported() -> None:
    import daily_report_agent

    assert daily_report_agent.__name__ == "daily_report_agent"
