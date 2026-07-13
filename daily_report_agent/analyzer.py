"""
分析器: 把 StockData 组装成中文 prompt, 调用 LLM 得到三段式分析。
prompt 结构借鉴 fingpt/FinGPT_Forecaster/prompt.py 的 SYSTEM_PROMPT + 组装方式,
翻译成中文并调整为"个股日报"用途。
"""
from __future__ import annotations

from daily_report_agent.datasource.base import StockData
from daily_report_agent.ingestion.adapters import stockdata_to_analysis_input
from daily_report_agent.llm_client import LLMClient
from daily_report_agent.models.analysis import AnalysisInput

SYSTEM_PROMPT = (
    "你是一位资深的证券市场分析师。请基于给定的公司简介、近期新闻和行情表现, "
    "分析该标的的利好因素和潜在风险, 并对下一周的股价走势给出判断和分析。"
    "请严格按以下格式用中文输出:\n\n"
    "【利好因素】\n1. ...\n2. ...\n\n"
    "【潜在风险】\n1. ...\n2. ...\n\n"
    "【下周展望与分析】\n判断: (看涨/看跌/中性)\n分析: ...\n"
)


def build_user_prompt(data: StockData) -> str:
    """把一只股票的数据拼成 user prompt。"""
    trend = "上涨" if data.pct_change >= 0 else "下跌"
    lines = []
    if data.intro:
        lines.append(f"【标的简介】\n{data.intro}\n")
    lines.append(
        f"从 {data.start_date} 到 {data.end_date}, {data.name or data.symbol} 的股价"
        f"{trend} {abs(data.pct_change):.2f}% (从 {data.start_price:.2f} 到 {data.end_price:.2f})。\n"
    )
    if data.news:
        lines.append("这段时间的相关新闻如下:\n")
        for n in data.news:
            summary = f"\n[摘要]: {n.summary}" if n.summary else ""
            lines.append(f"[{n.date}][标题]: {n.headline}{summary}")
    else:
        lines.append("这段时间没有检索到相关新闻。")
    lines.append(
        f"\n请基于 {data.end_date} 之前的全部信息, 先分析 {data.name or data.symbol} "
        "的 2-4 个最重要的利好因素和潜在风险(尽量从新闻中推断), "
        "然后对下一周的股价走势做出判断和分析。"
    )
    return "\n".join(lines)


def _format_optional_number(value: float | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def build_analysis_prompt(data: AnalysisInput) -> str:
    """为标准输入构建 prompt；缺失字段只作说明，不生成零值。"""
    name = data.security.name or data.security.symbol
    lines = []

    if data.profile_text:
        lines.append(f"【标的简介】\n{data.profile_text}\n")
    else:
        lines.append("【标的简介】\n公司简介数据缺失，本次不使用简介信息。\n")

    market_lines: list[str] = []
    if data.price_window is not None:
        start_price = _format_optional_number(data.price_window.start_price)
        end_price = _format_optional_number(data.price_window.end_price)
        pct_change = _format_optional_number(data.price_window.period_pct_change)
        if start_price is not None:
            market_lines.append(f"起始价格：{start_price}")
        if end_price is not None:
            market_lines.append(f"结束价格：{end_price}")
        if pct_change is not None:
            market_lines.append(f"区间涨跌幅：{pct_change}%")
    if not market_lines and data.market_snapshots:
        for snapshot in reversed(data.market_snapshots):
            price = _format_optional_number(snapshot.price)
            pct_change = _format_optional_number(snapshot.pct_change)
            if price is not None:
                market_lines.append(f"最新价格：{price}")
            if pct_change is not None:
                market_lines.append(f"快照涨跌幅：{pct_change}%")
            if market_lines:
                break

    if market_lines:
        lines.append("【可用行情】\n" + "\n".join(market_lines) + "\n")
    else:
        lines.append("行情数据缺失，本次仅根据其他可用信息进行分析。\n")

    if data.news:
        lines.append("这段时间的相关新闻如下:\n")
        for item in data.news:
            summary = f"\n[摘要]: {item.summary}" if item.summary else ""
            published_date = item.published_at.date().isoformat()
            lines.append(f"[{published_date}][标题]: {item.title}{summary}")
    else:
        lines.append("这段时间没有可用的新增新闻。")

    if data.warning_issues:
        lines.append(
            f"\n数据质量提示：存在 {len(data.warning_issues)} 项非阻断告警，"
            "本次仅使用以上可用数据。"
        )

    lines.append(
        f"\n请基于以上可用信息, 先分析 {name} 的 2-4 个最重要的利好因素和"
        "潜在风险(尽量从新闻中推断), 然后对下一周的股价走势做出判断和分析。"
    )
    return "\n".join(lines)


def analyze_input(
    analysis_input: AnalysisInput,
    llm: LLMClient,
    *,
    _user_prompt: str | None = None,
) -> str:
    """分析标准输入；阻断错误或完全无有效数据时不调用 LLM。"""
    if analysis_input.blocking_issues:
        return "_数据不足：存在阻断性数据错误，未执行方向性分析。_"
    if not analysis_input.has_news and not analysis_input.has_market_data:
        return "_数据不足：没有可用于分析的新闻或行情数据，未执行方向性分析。_"
    try:
        return llm.chat(
            SYSTEM_PROMPT,
            _user_prompt or build_analysis_prompt(analysis_input),
        )
    except Exception as e:
        return f"_LLM 分析失败: {e}_"


def _can_use_legacy_prompt(data: StockData, analysis_input: AnalysisInput) -> bool:
    """完整旧数据继续使用原构建器，确保其 prompt 逐字兼容。"""
    return bool(
        data.intro
        and data.news
        and analysis_input.has_market_data
        and len(analysis_input.news) == len(data.news)
    )


def analyze_stockdata(
    data: StockData,
    llm: LLMClient,
) -> tuple[AnalysisInput, str]:
    """兼容旧 DTO，并返回供 section DTO 使用的同一份标准输入。"""
    analysis_input = stockdata_to_analysis_input(data)
    legacy_prompt = (
        build_user_prompt(data) if _can_use_legacy_prompt(data, analysis_input) else None
    )
    return analysis_input, analyze_input(
        analysis_input,
        llm,
        _user_prompt=legacy_prompt,
    )


def analyze(data: StockData, llm: LLMClient) -> str:
    """保留旧公开入口；内部统一转换为 AnalysisInput 后分析。"""
    _, result = analyze_stockdata(data, llm)
    return result
