# 主链路迁移与证据驱动优化实施方案

> 状态：**Active — 当前优先路线**
> 文档版本：v2.0
> 制定日期：2026-07-28
> 适用项目：`daily_report_agent`
> 基线：`origin/main` `1f2c8ffd2f498442e3652c1aa3c3d2a793a766a3`
> 规划起点：P1-04B HEAD `ae55b9f2ca674a6744dd30c91e3316f400ce41a2`

## 1. 决策摘要

P1-04B 已完成 CNInfo Profile 纯离线骨架、synthetic Fixture、本地双版本回归和远端
双版本门禁，但没有在线 Transport、observed Fixture 或生产接入。Draft PR
[#6](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/pull/6) 保持未合并。

从本计划生效起，项目暂时冻结净新增 Provider 和信息类型开发，优先解决以下关键路径：

1. 建立可回退的新旧链路切换机制；
2. 让已具备证据的腾讯行情和完成在线门禁后的东财新闻进入新 Provider 编排；
3. 建立 Retry、Fallback、限流和 Circuit Breaker 高可用基座；
4. 用带证据引用的结构化分析替代单次自由文本分析；
5. 通过处理账本实现增量分析，停止重复消费相同新闻；
6. 完成报告审计、推送优化、历史 Replay 和七个交易日 Shadow 验收；
7. 只有在新链路达到验收门槛后，才切换默认路由并逐步删除旧链路。

本计划不授权 P1-04C 在线观察，不授权调用 CNInfo、Eastmoney 或其他 Provider API，也
不改变当前默认配置。每个在线动作仍须满足对应契约文档中的许可确认与独立授权门禁。

## 2. 当前事实基线

### 2.1 已完成能力

- Python 包、CLI、普通/固定日期 dry-run 和离线 CI 已建立；
- 标准 `Security`、`NewsItem`、`MarketSnapshot`、`PriceWindow`、`DataIssue` 和
  `AnalysisInput` 已建立；
- SQLite migration、事务、幂等新闻/行情写入、PipelineRun 和 ProviderCall 已建立；
- Tencent QuoteProvider 已通过离线契约、受控在线验证、五日 Shadow 和候选门禁；
- Eastmoney NewsProvider 已完成纯离线 Transport/Parser/Provider、synthetic 契约和
  存储幂等验证；
- Eastmoney/CNInfo Profile 均有纯离线骨架，但都没有可用于生产的在线证据；
- Provider 安全指标事件和固定 JSON 日志已接入腾讯 Shadow；
- P1-04B 在 Python 3.10.20/3.13.9 均为 `521 passed`，Push 和 PR Actions 双矩阵通过。

### 2.2 当前生产链路

```text
DataSource
→ StockData
→ AnalysisInput
→ 可选 SQLite 旁路
→ 单次自由文本 Analyzer
→ Markdown Report
→ Notifier
```

新的 Provider 尚未接管主链路。腾讯行情只运行在默认关闭的 Shadow 旁路，Eastmoney
News 和 CNInfo/Eastmoney Profile 都没有生产在线 Transport 或正式路由。

### 2.3 必须保留的金融语义

`TencentQuoteProvider` 返回单点 `MarketSnapshot`，旧 A 股行情返回日期区间
`PriceWindow`。两者不能直接互换：

```text
MarketSnapshot.pct_change ≠ PriceWindow.period_pct_change
```

因此“腾讯行情接管”首先表示接管当前行情快照能力，不表示可以把腾讯当日涨跌幅静默写入
报告的多日区间涨跌字段。在独立历史行情 Provider 完成前，正式编排必须同时保留：

- 腾讯：当前单点行情；
- 兼容适配器：旧历史行情形成的 `PriceWindow`；
- 明确的数据来源和质量状态。

## 3. 范围冻结

### 3.1 当前允许

- Provider Registry、Router、重试、限流、降级和熔断基础设施；
- 旧 DataSource 兼容适配器和新旧链路双跑；
- 腾讯行情正式编排设计和离线/Shadow 验收；
- Eastmoney News 生产接入所必需的在线门禁、Transport 和 observed Fixture；
- 标准事件模型、结构化 LLM 输出、处理账本和增量分析；
- 报告证据引用、健康卡片、通知审计和 Telegram 分段/附件；
- 历史 Replay、Shadow Run、可观测性和成本统计；
- 与上述工作直接相关的 migration、测试、文档和安全检查。

### 3.2 暂停

- P1-04C CNInfo 在线观察和在线 Transport；
- 新 Profile Provider；
- P3 第二新闻源、公告、市场快讯、研报、龙虎榜等新信息类型；
- Embedding、向量数据库、复杂多智能体和 Web 管理后台；
- 自动交易、仓位建议和高频信号。

真正的跨来源新闻 Fallback 需要独立的第二新闻源。旧
`akshare.stock_news_em` 与新 Eastmoney NewsProvider 共享东财上游，不能被描述成独立
冗余。本阶段可以完成路由能力，但不得伪报已经具备跨来源新闻高可用。

## 4. 目标架构

```text
Watchlist / Replay Range
→ CollectionOrchestrator
  → ProviderRegistry
  → ProviderRouter
    → Retry / Rate Limit / Circuit Breaker
    → Primary Provider
    → Partial-result Fallback
  → Normalization / Quality Gate
  → SQLite Evidence Store
→ IncrementalProcessingLedger
→ StructuredEventAnalyzer
  → Pydantic Validation
  → Evidence Reference Validation
→ Event Store
→ Auditable Report
→ Notification Dispatcher
```

新旧切换采用绞杀者模式，不进行一次性替换：

```text
legacy
  仅运行旧链路

provider_shadow
  旧链路生成正式报告
  新链路采集、分析和保存对比结果，但不通知

provider_primary
  新链路生成正式报告
  允许按明确策略回退旧兼容适配器
```

建议配置形态：

```yaml
pipeline:
  data_route: legacy
  fallback_to_legacy: true
  shadow_compare_enabled: false
```

初次实现和合并时默认值必须保持 `legacy`；不得因为代码存在而自动切换生产行为。

## 5. 正式 Provider 路由

### 5.1 Registry

Registry 只声明能力，不执行网络请求。每个注册项至少包含：

- `provider_id`
- `capability`
- `market`
- `priority`
- `enabled`
- `online_evidence_level`
- `supports_batch`
- `timeout_seconds`
- `fallback_group`

只有通过对应在线门禁的 Provider 才能进入 `provider_primary` 候选列表。纯离线 Provider
可以注册为 `offline_only`，但 Router 必须拒绝把它用于生产请求。

### 5.2 Retry

重试必须按错误类别执行，不能统一“失败重试三次”：

| 错误类别 | 策略 |
|---|---|
| timeout、502、503、临时连接错误 | 有上限的指数退避和抖动 |
| 429 | 尊重安全的等待提示，否则切换或失败 |
| 401、403、blocked | 不盲目重试，记录并触发熔断 |
| parser/protocol drift | 不重试，标记协议漂移 |
| validation | 不重试，拒绝污染标准模型 |
| empty | 按能力、市场时间和 freshness 规则判断 |
| partial | 只为缺失证券或缺失能力执行 Fallback |

必须设置总请求预算。单个 Provider 的重试不能无限延长整份日报。

### 5.3 Fallback

Fallback 的输入和输出保持标准模型，不允许降级逻辑重新拼接 `StockData.error`。

第一阶段候选策略：

- 当前快照：腾讯为主候选，旧行情兼容适配器为临时备用；
- 历史区间：继续使用旧历史行情兼容适配器，直到独立历史行情 Provider 通过门禁；
- A 股新闻：Eastmoney 新 Provider 只有在 observed Fixture 和受控在线验证通过后才能
  成为主候选；
- Profile：保持旧行为或明确缺失，不启用 Eastmoney/CNInfo 离线骨架；
- 美股：先包装现有 Finnhub/yfinance 为兼容 Provider，不在本轮扩展新来源。

Fallback 后必须保留：

- 实际来源；
- 尝试顺序；
- 每次调用状态；
- 最终选择原因；
- 数据 freshness；
- 未满足的数据质量问题。

### 5.4 Circuit Breaker

熔断粒度为 `provider_id + operation`，避免一个能力失败导致整个 Provider 被禁用。

Cron 每次运行都是新进程，纯内存状态无法跨运行生效。建议在 SQLite 中持久化：

- `state`: `closed/open/half_open`
- `consecutive_failures`
- `opened_at`
- `open_until`
- `last_failure_at`
- `last_success_at`
- `last_error_code`

`KeyboardInterrupt` 和 `SystemExit` 不计入 Provider 失败。日志、指标或熔断状态保存失败
不得覆盖原始 Provider 结果。

### 5.5 Rate Limit 与 Cache

- 限流按 Provider 和 operation 独立计算；
- 批量接口优先合并请求；
- 相同运行内避免重复请求同一证券和时间窗口；
- 缓存必须带 source、fetched_at、expires_at 和参数指纹；
- 过期缓存只能作为明确标注的 degraded result，不能伪装成实时数据；
- Replay 严禁命中当前在线缓存。

## 6. 新旧链路迁移

### 6.1 兼容边界

迁移期间允许旧 DataSource 通过兼容适配器输出标准 `CollectedSecurityData`，但新 Provider
不得反向转换为 `StockData`。目标方向固定为：

```text
Legacy Adapter ─┐
                ├→ CollectedSecurityData / AnalysisInput
New Providers ──┘
```

### 6.2 Shadow 对比

`provider_shadow` 每次运行至少比较：

- 请求证券数与返回证券数；
- 行情 freshness、价格和涨跌字段；
- 新闻数量、内容哈希和 URL 覆盖率；
- 数据 Issue 数和严重级别；
- Provider 成功率、重试数、Fallback 数和耗时；
- 旧/新分析输入的差异；
- 结构化分析成功率和未引用结论数；
- 报告是否生成，但 Shadow 报告不得通知正式渠道。

对比结果必须写入独立表或独立命名空间，不能覆盖正式 PipelineRun 语义。

### 6.3 删除旧链路门禁

旧 DataSource 和自由文本 Analyzer 只有同时满足以下条件才允许删除：

1. 新链路连续七个交易日无人值守；
2. 数据完整度不低于旧链路；
3. 单源失败不会阻断其他标的和整份报告；
4. 结构化输出 Schema 验证成功率达到约定门槛；
5. 未引用事实为 0；
6. Replay 可以复现指定运行；
7. 通知去重和失败隔离通过；
8. 有经过测试的一键配置回退；
9. 双 Python 版本离线门禁和受控在线门禁均通过；
10. 用户明确批准切换默认路由和后续删除。

## 7. 结构化、证据驱动分析

### 7.1 输出模型

LLM 只能返回候选业务字段，最终 ID、URL 解析和合法性由程序控制。建议使用 Pydantic
定义版本化 Schema：

```python
class Fact:
    fact_id: str
    statement: str
    evidence_ids: list[str]


class Inference:
    statement: str
    based_on_fact_ids: list[str]
    confidence: float
    invalidation_conditions: list[str]


class AnalyzedEvent:
    schema_version: str
    event_type: str
    severity_score: int
    facts: list[Fact]
    inferences: list[Inference]
    risks: list[str]
    affected_symbols: list[str]
    contradictory_evidence_ids: list[str]
    missing_evidence: list[str]
```

持久化元数据至少包括：

- 程序生成的稳定 `event_id`；
- `schema_version`
- `prompt_version`
- `model`
- `provider`
- `temperature`
- `analysis_cutoff_at`
- `generated_at`
- 输入/输出 Token；
- 预估成本；
- 完整证据 ID 集合；
- 数据质量评分。

### 7.2 ID 与证据规则

- LLM 只能引用输入中存在的 `news_id` 或其他 evidence ID；
- URL 不由 LLM 生成，由报告层根据 evidence ID 查库；
- 未知 evidence ID 使本次结构化结果验证失败；
- 最终 `event_id` 由程序根据事件类型、证券和规范化证据集合稳定生成；
- Fact 必须至少有一个有效证据；
- Inference 必须引用一个或多个 Fact；
- 事实、推断、风险、情景和失效条件不得混为一个自由文本字段；
- 新闻正文始终是不可信数据，不能作为程序或模型控制指令。

### 7.3 解析失败

不同 LLM 对 Function Calling 和 JSON Mode 的支持不一致，因此必须在程序端再次校验：

```text
模型返回
→ JSON 解析
→ Pydantic 校验
→ evidence ID 校验
→ 最多一次受限修复
→ 仍失败则记录失败，不进入正式结论
```

不得为了“让报告有内容”而把校验失败的自由文本回填到正式结构化报告。

## 8. 增量状态与处理账本

### 8.1 不使用 `id > last_processed_id`

`NewsItem.id` 可能是哈希或 UUID，不具备可靠顺序；新闻也可能延迟到达。单一时间 Watermark
同样可能漏掉补录数据。因此正确性以处理账本为准，时间游标只用于查询优化。

建议增加：

```text
analysis_processing_records
- news_id
- analyzer_version
- prompt_version
- model
- status
- attempt_count
- processed_at
- event_id
- error_code
```

唯一键建议覆盖：

```text
(news_id, analyzer_version, prompt_version, model)
```

这样能够支持：

- 只处理未成功消费的新证据；
- 延迟到达新闻；
- 失败项安全重试；
- Prompt、Schema 或模型升级后的受控重放；
- 判断每条新闻为何未进入事件或报告。

### 8.2 事件更新

增量新闻可能创建新事件，也可能补充、澄清或反驳已有事件。第一版采用确定性候选规则：

1. external ID 或内容哈希做精确去重；
2. 同一证券、事件类型和限定时间窗口形成候选事件；
3. 结构化分析返回 `new` 或 `update_candidate`；
4. 程序校验证据后创建事件版本；
5. 不覆盖历史版本，保留完整演进记录。

第一版不要求 Embedding 或向量数据库。

## 9. 报告、审计和通知

### 9.1 报告

每一条 Fact 必须显示可解析的引用标签，例如：

```text
[来源：eastmoney:news:<news_id>]
```

报告层再把标签解析成来源名称、发布时间和原文 URL。没有有效引用的 Fact 不得进入正式
报告。

报告底部增加系统健康卡片：

- PipelineRun 状态；
- 新增、重复、失败新闻数；
- 新建和更新事件数；
- Provider 调用成功率、p95 延迟、重试和 Fallback 数；
- 熔断状态；
- 结构化输出成功率；
- 未引用事实数；
- 输入/输出 Token；
- 按版本化价格表估算的成本；
- 数据截止时间和生成时间。

### 9.2 Telegram

不再截断一份长报告后直接发送。建议：

1. 首条发送运行状态和核心摘要；
2. 单独发送高严重度事件，附证据 URL；
3. 完整 Markdown 作为 Document 上传；
4. 保存外部消息 ID、状态和安全错误码；
5. 使用事件版本和渠道作为幂等键，避免重复预警。

邮件可以发送 HTML 摘要和 Markdown 附件。Web 链接不是第一期必需项，避免提前引入托管、
访问控制和隐私边界。

## 10. Replay

建议 CLI：

```bash
daily-report-agent replay \
  --from 2026-07-01 \
  --to 2026-07-20 \
  --llm-mode cached
```

支持：

- `cached`：使用历史结构化分析，验证事件、报告和通知渲染；
- `rerun`：使用指定模型、Prompt 和 Schema 重新分析，供版本比较。

Replay 必须满足：

- 只读取 `fetched_at <= replay_cutoff` 且 `published_at <= replay_cutoff` 的证据；
- 不请求当前 Provider；
- 默认不发送通知；
- 不推进正式处理账本；
- 不覆盖历史报告；
- 写入独立 replay run 和输出目录；
- 明确记录模型、Prompt、Schema、价格表和代码版本；
- `rerun` 产生的费用与正式运行分开统计。

## 11. 七个交易日 Shadow

只有正式路由基础和结构化增量分析都完成后才启动。运行环境可使用现有 Ubuntu 定时环境，
但部署和联网仍需单独授权。

每天记录：

- 调度是否准时；
- 新旧链路数据完整度；
- Provider 成功率、错误类别、重试、Fallback、熔断和恢复；
- 行情 freshness 和新闻延迟；
- 重复处理数；
- 结构化校验失败数；
- 无效 evidence ID 和未引用事实数；
- Token、成本和总耗时；
- 报告、附件和测试通知结果；
- 正式旧链路是否保持不受影响。

七日通过门槛建议：

- 7/7 交易日均产生 Shadow 报告；
- 正式旧链路 7/7 不受影响；
- 单源失败不会阻断其他标的或整份报告；
- 未引用事实为 0；
- 无非法 evidence ID；
- 无重复正式通知；
- 无未经解释的数据缺失或 freshness 退化；
- 费用和耗时在批准预算内；
- 所有审计清单和数据库一致性检查通过。

七日通过只允许提交切换评审，不自动切换默认配置。

## 12. 实施里程碑

### M0：范围冻结与基线

- [x] P1-04B 离线骨架和双版本本地/远端门禁完成；
- [ ] Draft PR #6 完成审查并决定是否合并；
- [ ] 冻结 P1-04C、P3 和其他新 Provider；
- [ ] 建立本计划为当前活跃路线；
- [ ] 固定主链路迁移前回归哈希、数据库 schema 和报告样本。

### M1：路由基座

- [x] M1-01 定义纯离线 Registry、RoutePolicy、RouteResult 和确定性选择逻辑；
- [x] 增加 `legacy/provider_shadow/provider_primary` 配置解析与阶段门禁；
- [ ] 实现可调用 Provider 的 Router 和正式/Shadow 编排；
- [ ] 实现错误分类重试、请求预算和部分结果降级；
- [ ] 实现持久化 Circuit Breaker；
- [ ] 实现限流、缓存 freshness 和审计事件；
- [ ] 使用纯离线 Fake Provider 覆盖所有状态转换；
- [x] 默认仍为 `legacy`，另外两种模式在业务副作用前明确拒绝。

### M2：行情迁移

- [ ] 腾讯单点行情进入 `provider_shadow`；
- [ ] 旧历史行情通过兼容适配器继续生成 `PriceWindow`；
- [ ] 禁止把当日涨跌写入多日区间涨跌；
- [ ] 完成批量、部分缺失、Fallback 和熔断验收；
- [ ] 形成旧/新行情差异报告。

### M3：新闻迁移

- [ ] 完成 Eastmoney News 自动化访问和 Fixture 保存许可确认；
- [ ] 经单独授权制作最小脱敏 observed Fixture；
- [ ] 实现在线 Transport 和受控在线门禁；
- [ ] 接入 Router、SQLite 和 `provider_shadow`；
- [ ] 明确当前尚无真正跨来源 Fallback；
- [ ] 验证旧/新新闻数量、URL、时间、哈希和缺失差异。

### M4：新主链路双跑

- [ ] 新链路直接生成标准 `AnalysisInput`；
- [ ] 新 Provider 不反向转换为 `StockData`；
- [ ] 旧链路继续生成正式报告；
- [ ] 新链路生成独立 Shadow 报告和差异记录；
- [ ] PipelineRun、ProviderCall 和质量 Issue 语义一致。

### M5：结构化分析

- [ ] 增加版本化 Pydantic Schema；
- [ ] 实现 Fact/Inference/Risk/Scenario 分离；
- [ ] 实现 evidence ID 和 Fact 引用校验；
- [ ] 程序生成稳定 EventID；
- [ ] 记录模型、Prompt、Token、成本和截止时间；
- [ ] 最多一次受限修复，失败结果不进入正式报告。

### M6：增量状态

- [ ] 增加处理账本 migration 和 Repository；
- [ ] 只处理尚未成功消费的证据；
- [ ] 支持延迟到达、失败重试和版本化重跑；
- [ ] 增加事件版本和增量更新；
- [ ] 同一新闻不再每日重复消耗 LLM。

### M7：报告与通知

- [ ] 每条 Fact 带有效来源引用；
- [ ] 增加健康卡片和数据截止时间；
- [ ] Telegram 摘要、高严重度事件和 Document；
- [ ] 邮件 HTML 摘要和 Markdown 附件；
- [ ] 通知结果持久化和幂等去重。

### M8：Replay

- [ ] 增加只读历史查询；
- [ ] 支持 `cached` 和 `rerun`；
- [ ] 防止未来数据泄漏；
- [ ] Replay 与正式状态、通知和成本隔离；
- [ ] 建立 Prompt/模型对比报告。

### M9：七日 Shadow 与切换

- [ ] 连续七个交易日无人值守；
- [ ] 每日审计与汇总通过；
- [ ] 提交 `provider_primary` 切换评审；
- [ ] 用户批准后切换默认路由；
- [ ] 保留旧兼容回退至少一个稳定周期；
- [ ] 再次批准后删除旧 DataSource 和自由文本 Analyzer。

## 13. 每个里程碑的通用完成定义

1. 正常、空、部分、失败和恢复路径有离线测试；
2. pytest 和 dry-run 默认不联网；
3. 在线动作使用独立入口、显式门禁和单独授权；
4. Python 3.10/3.13 完整测试、compileall 和 `git diff --check` 通过；
5. 默认配置和未获准生产行为保持不变；
6. 错误、日志、Fixture 和报告不泄露凭据或未脱敏正文；
7. migration 可重复、事务安全并有回滚测试；
8. Prompt、Schema、模型和输出版本可追溯；
9. 文档只记录已经发生且可复现的事实；
10. 变更按单一职责提交并可独立回退。

## 14. 风险与控制

| 风险 | 控制 |
|---|---|
| 一次性替换导致日报中断 | 三模式切换、双跑和兼容回退 |
| Eastmoney 新旧实现共享上游 | 不把它们描述为独立 Fallback |
| 腾讯单点行情污染区间语义 | 保持 `MarketSnapshot`/`PriceWindow` 分离 |
| 重试放大限流或封禁 | 错误分类、总预算、抖动和熔断 |
| Cron 重启丢失熔断状态 | SQLite 持久化 |
| LLM 伪造 ID 或 URL | 程序生成 ID、查库解析 URL、严格校验 |
| 简单 Watermark 漏掉迟到新闻 | 处理账本作为正确性来源 |
| Replay 引入未来信息 | 双时间截止条件和只读证据集 |
| Shadow 结果污染生产状态 | 独立 run 类型、表或命名空间 |
| 过早删除旧链路 | 七日验收、回退验证和用户批准三重门禁 |

## 15. 文档真相源

从本计划生效起：

- `README.md`：当前稳定能力、运行方式和最新验收状态；
- 本文件：当前活跃优化路线、实施顺序和切换门禁；
- `CHANGELOG.md`：已发生的用户可见变化；
- `docs/*_contract.md`：字段、证据和 Provider 安全边界；
- `docs/parallel_development_plan.md`：B4/P1/P2 并行开发历史与已完成门禁；
- `daily_report_agent/OPTIMIZATION_PLAN.md`：2026-07-13 历史方案，不再作为当前任务状态。

若文档冲突，以代码和可复现测试为事实依据，再由 `README.md` 和本文件同步修正。
