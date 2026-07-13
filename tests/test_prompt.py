from daily_report_agent.analyzer import build_user_prompt
from daily_report_agent.datasource.base import StockData


def test_prompt_builds_when_external_data_is_missing() -> None:
    prompt = build_user_prompt(StockData(symbol="TEST", name="测试标的"))

    assert "测试标的" in prompt
    assert "没有检索到相关新闻" in prompt
    assert "0.00" in prompt
