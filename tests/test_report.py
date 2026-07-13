from daily_report_agent.report import render


def test_markdown_report_renders_fixed_data() -> None:
    content = render(
        [
            {
                "symbol": "TEST",
                "name": "测试标的",
                "market": "us",
                "pct_change": -1.25,
                "analysis": "固定分析结果",
            }
        ],
        "2026-07-13",
    )

    assert "# 每日股市分析报告 · 2026-07-13" in content
    assert "测试标的 (TEST) · 美股" in content
    assert "📉 -1.25%" in content
    assert "固定分析结果" in content
    assert "免责声明" in content


def test_report_distinguishes_missing_market_from_real_zero() -> None:
    content = render(
        [
            {
                "symbol": "MISSING",
                "name": "缺失行情",
                "market": "us",
                "pct_change": None,
                "analysis": "数据不足",
            },
            {
                "symbol": "FLAT",
                "name": "真实平盘",
                "market": "cn",
                "pct_change": 0.0,
                "analysis": "固定分析",
            },
        ],
        "2026-07-13",
    )

    assert "缺失行情 (MISSING) · 美股\n\n区间涨跌: 行情数据缺失" in content
    assert "真实平盘 (FLAT) · A股\n\n区间涨跌: 📈 +0.00%" in content
