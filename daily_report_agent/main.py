"""
每日股市新闻分析报告智能体 — 编排入口。

流程: 读 config → 遍历 watchlist 抓数据 → LLM 分析 → 汇总 Markdown → 存档 → 推送。

用法:
    python -m daily_report_agent                # 完整跑一次(抓数据 + LLM + 推送)
    python -m daily_report_agent --no-notify    # 跑但不推送
    python -m daily_report_agent --dry-run      # 离线占位数据验证链路(无需 key)
    python -m daily_report_agent --config other.yaml
"""
from __future__ import annotations

import os
import sys
import argparse
from datetime import datetime

import yaml

from . import analyzer, report
from .config import parse_provider_routing_settings
from .datasource.base import get_source, StockData
from .ingestion.adapters import stockdata_to_analysis_input
from .models.analysis import AnalysisInput
from .pipeline.runner import (
    finish_storage_run,
    persist_analysis_input,
    start_run_context,
)
from .pipeline.tencent_quote_shadow import maybe_run_tencent_quote_shadow
from .providers.routing import require_route_mode_enabled


DRY_RUN_ANALYSIS = (
    "【利好因素】\n1. (dry-run 占位)\n\n"
    "【潜在风险】\n1. (dry-run 占位)\n\n"
    "【下周展望与分析】\n判断: 中性\n分析: dry-run 模式未调用 LLM。"
)


def load_env():
    """轻量读取同目录 .env(不引入 python-dotenv 依赖)。"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            # 去掉行尾注释与引号
            val = val.split("#", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_llm(cfg: dict):
    from .llm_client import LLMClient
    llm_cfg = cfg.get("llm", {})
    return LLMClient(
        provider=llm_cfg.get("provider", "deepseek"),
        base_url=llm_cfg.get("base_url"),
        model=llm_cfg.get("model"),
        temperature=llm_cfg.get("temperature", 0.7),
        max_tokens=llm_cfg.get("max_tokens", 2000),
    )


def _section_pct_change(analysis_input: AnalysisInput) -> float | None:
    """从标准输入生成旧报告 section 所需的兼容涨跌字段。"""
    if (
        analysis_input.price_window is not None
        and analysis_input.price_window.period_pct_change is not None
    ):
        return analysis_input.price_window.period_pct_change
    for snapshot in reversed(analysis_input.market_snapshots):
        if snapshot.pct_change is not None:
            return snapshot.pct_change
    return None


def run(config_path: str, do_notify: bool, dry_run: bool):
    cfg = load_config(config_path)
    if not isinstance(cfg, dict):
        raise ValueError("配置文件顶层必须是 YAML 映射")
    route_settings = parse_provider_routing_settings(cfg)
    # M1-01 只建立离线契约；非 legacy 模式在凭据和其他副作用前明确失败。
    require_route_mode_enabled(route_settings.mode)
    # dry-run 不读取本机 .env，确保测试结果与用户密钥完全隔离。
    if not dry_run:
        load_env()
    data_cfg = cfg.get("data", {})
    news_days = data_cfg.get("news_days", 7)
    max_news = data_cfg.get("max_news_per_stock", 15)

    run_context = start_run_context(cfg, config_path=config_path, dry_run=dry_run)
    partial = run_context.storage_failed

    try:
        maybe_run_tencent_quote_shadow(
            config=cfg,
            watchlist=cfg.get("watchlist", ()),
            run_context=run_context,
            dry_run=dry_run,
        )
        llm = None if dry_run else build_llm(cfg)
        sections = []
        for item in cfg.get("watchlist", []):
            market = item.get("market")
            symbol = str(item.get("symbol"))
            name = item.get("name", "")
            print(f"[抓取] {market}:{symbol} {name} ...")

            if dry_run:
                data = StockData(symbol=symbol, name=name, market=market,
                                 end_price=100.0, start_price=98.0, pct_change=2.04)
                text = DRY_RUN_ANALYSIS
                section_pct_change = data.pct_change
            else:
                try:
                    data = get_source(market).fetch(symbol, name, news_days, max_news)
                except Exception as e:
                    data = StockData(symbol=symbol, name=name, market=market,
                                     error=f"数据源初始化失败: {e}")
                analysis_input = stockdata_to_analysis_input(data)
                if not persist_analysis_input(run_context, analysis_input):
                    partial = True
                if data.error:
                    partial = True
                text = analyzer.analyze(data, llm)
                section_pct_change = _section_pct_change(analysis_input)

            sections.append({
                "symbol": symbol, "name": data.name or name, "market": market,
                "pct_change": section_pct_change, "analysis": text,
            })

        date_str = datetime.now().strftime("%Y-%m-%d")
        content = report.render(sections, date_str)
        path = report.save(content, date_str)
        print(f"[报告] 已生成: {path}")

        if do_notify and not dry_run:
            # 延迟导入通知模块，dry-run 不加载任何通知客户端。
            from . import notifier
            notifier.notify(f"每日股市分析报告 · {date_str}", content, cfg.get("notify", {}))
        else:
            print("[推送] 已跳过")
    except Exception as exc:
        finish_storage_run(
            run_context,
            "failed",
            error_summary=f"{type(exc).__name__}: 日报运行失败",
        )
        raise
    else:
        finish_storage_run(
            run_context,
            "partial" if partial else "success",
            error_summary="部分数据或存储步骤失败" if partial else None,
        )

    return path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="每日股市新闻分析报告智能体")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.yaml"))
    ap.add_argument("--no-notify", action="store_true", help="不推送")
    ap.add_argument("--dry-run", action="store_true", help="不联网/不调 LLM, 验证链路")
    return ap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args.config, do_notify=not args.no_notify, dry_run=args.dry_run)
    except Exception as exc:
        print(f"[错误] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def cli() -> int:
    """console script 与 python -m 的统一入口。"""
    return main()


if __name__ == "__main__":
    raise SystemExit(cli())
