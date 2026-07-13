# daily_report_agent 开发进度

日期：2026-07-13  
下一开发日：2026-07-14  
当前节点：阶段 1-C2 已完成，明天开始阶段 2。

## 1. 今日完成概览

今天完成了项目从“无法稳定启动的脚本”到“可安装、可测试、具备标准模型与可选 SQLite 存储”的基础升级。

当前生产链路为：

```text
DataSource
→ StockData
→ AnalysisInput
→ 可选 SQLite 旁路（默认关闭）
→ Analyzer
→ Report
→ Notifier
```

## 2. 阶段 0：恢复可运行性

已完成：

- 修复 Python 包结构和导入路径；
- 增加 `pyproject.toml` 和正式包定义；
- 支持 `python -m daily_report_agent`；
- 建立真正离线、无需 API Key 的 dry-run；
- 增加 pytest 基线；
- 更新安装、启动和测试文档；
- 验证 Python 3.10 与 Python 3.13。

## 3. 阶段 1：可靠数据底座

### 3.1 阶段 1-A：架构审计

- 梳理 `StockData`、新闻、行情、Analyzer 和 Report 的现有数据流；
- 明确标准模型、质量问题和 SQLite 的最小迁移路线；
- 确认采用旁路迁移，避免一次性重写生产链路。

### 3.2 阶段 1-B1：标准模型

新增：

- `Security`；
- `NewsItem`；
- `MarketSnapshot`；
- `PriceWindow`；
- `DataIssue`；
- `CollectedSecurityData`；
- Provider Protocol。

所有时间字段统一要求 timezone-aware，未知数值使用 `None`，不再用 `0` 表示缺失。

### 3.3 阶段 1-B2：旧模型适配层

完成：

- `StockData → CollectedSecurityData`；
- 稳定新闻内容哈希；
- 旧日期 UTC 标准化；
- 旧 `StockData.error` 转换为 warning；
- 缺失行情不再生成伪造的 `0%`；
- 增加独立质量判断。

### 3.4 阶段 1-B3：标准模型进入分析链路

完成：

- 新增不可变 `AnalysisInput`；
- 保留旧 `analyze(StockData, llm)` 公开入口；
- `StockData.error` 不再直接跳过整只股票；
- 行情失败但新闻存在时继续分析；
- 新闻失败但行情存在时继续分析；
- 完全无有效数据或存在阻断性 ERROR 时不调用 LLM；
- 报告区分未知行情与真实 `0.00%`；
- 完整数据 Prompt 和 dry-run 报告保持逐字兼容。

### 3.5 阶段 1-C1：SQLite 核心

新增：

- 显式 SQLite 连接与事务管理；
- `0001_initial` migration；
- UTC 时间序列化；
- Security、News、MarketSnapshot、PipelineRun、ProviderCall、RawResponse Repository；
- 幂等写入、外键、WAL、失败回滚；
- RawResponse 脱敏写入边界；
- Repository 单元测试和跨 Repository 事务测试。

数据库表：

```text
schema_migrations
securities
security_aliases
news_items
news_security_links
market_snapshots
pipeline_runs
provider_calls
raw_responses
```

未创建事件、分析结果或通知历史表。

### 3.6 阶段 1-C2：可选存储进入生产编排

正式配置新增：

```yaml
storage:
  enabled: false
  path: data/agent.db
```

完成：

- storage 默认关闭；
- 旧配置缺少 storage 字段时等价于关闭；
- dry-run 即使配置开启也不创建数据库；
- storage 关闭时不加载 SQLite runtime；
- storage 开启时记录 `pipeline_run`；
- 保存标准 Security、News 和 MarketSnapshot；
- 明确不保存 `PriceWindow`；
- 暂不伪造现有 akshare、yfinance 或 Finnhub 的 provider call；
- 数据库失败时降级为 warning，Analyzer 和 Report 继续运行；
- 运行状态支持 `success`、`partial` 和 `failed`。

## 4. 当前验证基线

最终验证结果：

- 测试总数：102；
- Python 3.13.9：102/102 通过；
- Python 3.10.20：102/102 通过；
- `python -m compileall -q daily_report_agent tests`：通过；
- `python -m daily_report_agent --dry-run`：退出码 0；
- dry-run 前后均未生成 `.db`、`.sqlite` 或 `.sqlite3`；
- 未读取真实 API Key；
- 未调用真实 LLM、网络数据源或通知。

回归哈希：

```text
完整数据 Prompt:
7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3

固定 dry-run 报告:
8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0
```

## 5. 当前明确未实施的内容

以下能力尚未实施，不能视为已经完成：

- 腾讯行情 Provider；
- 东财新闻 Provider；
- 巨潮公告 Provider；
- 财联社快讯 Provider；
- Provider 路由、重试、限流、缓存、熔断和降级；
- 增量抓取；
- 新闻近似去重和事件聚类；
- 事件、分析结果和通知历史存储；
- 结构化日志和在线监控；
- 报告中的真实来源与原文链接。

## 6. 明天：阶段 2 开发计划

阶段 2 目标是“接入核心 A 股数据能力”。建议先完成阶段 2-A 审计和第一个 Provider 的离线契约，再逐个扩展，避免同时引入多个不稳定接口。

建议顺序：

1. 审计 `a-stock-data` Skill、现有 Provider Protocol 和当前 A 股调用链；
2. 定义 Provider 返回契约、错误映射、限流与降级边界；
3. 优先实现腾讯批量行情 Provider；
4. 使用脱敏 Fixture 编写完全离线的契约测试；
5. 再接入东财个股新闻；
6. 将巨潮公告建模为独立来源类型；
7. 最后增加财联社和东财全球资讯备用源；
8. 接入 provider_calls 运行记录，但不得伪造调用；
9. 完成在线冒烟测试与字段变化检查；
10. 在证据真实可用后，再调整报告中的来源和原文链接展示。

阶段 2 的主要验收目标：

- A 股新闻至少有两个可切换来源；
- 公告作为独立类型进入标准模型；
- 单一来源失败不会阻断整份日报；
- 关键新闻具有真实来源和 URL；
- Provider 契约测试完全离线；
- 在线冒烟测试可以发现字段变化；
- 原有 102 项测试、Prompt 哈希和 dry-run 哈希继续保持。

## 7. 明天开始前检查

```bash
python -m pytest
python -m compileall -q daily_report_agent tests
python -m daily_report_agent --dry-run
```

开始阶段 2 前应再次确认：

- 默认配置仍为 `storage.enabled: false`；
- dry-run 未创建数据库；
- 单元测试不联网；
- Fixture 不包含 API Key、token、Cookie 或未脱敏响应；
- 不修改现有金融判断和 Prompt 业务语义；
- 每接入一个 Provider，都必须有独立契约测试和失败降级测试。
