"""
报告渲染: 把每只股票的分析汇总成一份 Markdown 日报, 存到 reports/YYYY-MM-DD.md。
"""
from __future__ import annotations

import os
from datetime import datetime

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")

DISCLAIMER = (
    "\n---\n\n"
    "> **免责声明**: 本报告由 AI 自动生成, 仅供学习研究, 不构成任何投资建议。"
    "据此操作风险自负, 投资前请咨询专业人士。\n"
)


def render(sections: list, date_str: str | None = None) -> str:
    """sections: list[dict], 每项含 symbol/name/market/pct_change/analysis。返回完整 Markdown。"""
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    parts = [f"# 每日股市分析报告 · {date_str}\n"]
    parts.append(f"共分析 {len(sections)} 只标的。\n")

    for s in sections:
        market_tag = {"us": "美股", "cn": "A股"}.get(s.get("market", ""), s.get("market", ""))
        title = f"{s.get('name') or s.get('symbol')} ({s.get('symbol')})"
        parts.append(f"\n## {title} · {market_tag}\n")
        pct = s.get("pct_change")
        if pct is None:
            parts.append("区间涨跌: 行情数据缺失\n")
        else:
            arrow = "📈" if pct >= 0 else "📉"
            parts.append(f"区间涨跌: {arrow} {pct:+.2f}%\n")
        parts.append(s.get("analysis", "_无分析_") + "\n")

    parts.append(DISCLAIMER)
    return "\n".join(parts)


def save(content: str, date_str: str | None = None) -> str:
    """写入 reports/YYYY-MM-DD.md, 返回文件路径。"""
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{date_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
