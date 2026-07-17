# daily_report_agent 开发进度

更新时间：2026-07-14
当前节点：阶段 2-B3-B1 已完成，阶段 2-B3-B2 连续人工观测待进行。

`daily_report_agent` 是一个面向多数据源、证据驱动分析的每日市场信息智能体。当前正式
链路继续使用既有 DataSource；新的 Provider 能力采用契约化、离线测试和旁路观察逐步
迁移，未经长期验证的数据源不会直接进入分析或报告。

当前正式链路为：

```text
DataSource
→ StockData
→ AnalysisInput
→ 可选 SQLite 旁路（默认关闭）
→ Analyzer
→ Report
→ Notifier
```

腾讯行情 Shadow 是独立旁路，不在上述正式分析数据流中：

```text
Tencent QuoteProvider
→ provider_calls
→ MarketSnapshot
→ 可选 SQLite（Shadow 默认关闭）
```

## 当前开发状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| 阶段 0：工程稳定化 | 已完成 | Python 包、CLI、离线 dry-run、基础测试 |
| 阶段 1-A：数据底座审计 | 已完成 | 完成旧数据模型、数据流和存储边界审计 |
| 阶段 1-B：标准模型与分析兼容 | 已完成 | 标准 Security、News、Market、Issue、AnalysisInput 已建立 |
| 阶段 1-C：SQLite 存储 | 已完成 | 标准数据持久化及可选生产接入 |
| 阶段 2-A：Provider 契约 | 已完成 | Quote、News、Profile Provider 契约及 Legacy Façade |
| 阶段 2-B1：腾讯 QuoteProvider 离线实现 | 已完成 | 代码映射、Parser、Fixture、离线契约测试 |
| 阶段 2-B2：腾讯受控在线验证 | 已完成 | 在线 Transport、编码、字段和状态 1/51 验证 |
| 阶段 2-B3-A：腾讯 Shadow 接入 | 已完成 | 默认关闭的旁路观测、ProviderCall 和 Snapshot 保存 |
| 阶段 2-B3-B1：单次真实 Shadow 验证 | 已完成 | 临时数据库真实请求和幂等验证 |
| 阶段 2-B3-B2：连续人工观测 | 待进行 | 连续 5～7 个交易日记录稳定性指标 |

### 当前基线

```text
Git:
fa85b709b17c2478a80d1101b803f1c93278171b

测试：
271 passed

验证环境：
Python 3.10.20
Python 3.13.9
```

回归哈希：

```text
完整数据 Prompt SHA-256:
7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3

固定日期 dry-run SHA-256:
8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0
```

这两个哈希分别用于发现 Prompt 业务语义和固定 dry-run 报告输出的意外变化。

## 已完成的基础能力

### 工程与离线运行

- 项目可安装为正式 Python 包并支持 `python -m daily_report_agent`；
- dry-run 无需 API Key，不调用真实 LLM、网络数据源或通知；
- Python 3.10 和 Python 3.13 均纳入验证；
- pytest 默认禁止网络访问，Provider 在线验证使用独立、显式入口。

### 标准数据与分析兼容

- 已建立 `Security`、`NewsItem`、`MarketSnapshot`、`PriceWindow`、`DataIssue`、
  `CollectedSecurityData` 和 `AnalysisInput`；
- 时间字段要求 timezone-aware，未知数值使用 `None`，真实 `0.0` 保持为零；
- 旧 `StockData` 通过适配层进入标准模型；
- 行情或新闻单侧失败时可继续分析，完全无有效数据时不调用 LLM；
- 旧分析入口、Prompt 和固定 dry-run 输出保持兼容。

### SQLite 与 Provider 契约

- SQLite 提供显式连接、事务、migration、外键和幂等写入；
- storage 默认关闭，dry-run 即使配置开启也不会创建数据库；
- 已建立 `QuoteProvider`、`NewsProvider`、`ProfileProvider`、标准 Provider 错误层和
  `LegacyDataSourceFacade`；
- Provider 契约测试完全离线，真实网络能力必须由受控入口显式开启；
- Provider 迁移边界和字段证据详见
  [`docs/provider_migration.md`](docs/provider_migration.md)。

## 腾讯财经 QuoteProvider

腾讯财经目前只负责 A 股单点行情快照 `MarketSnapshot`，已确认使用的语义包括：

- 当前价格；
- 昨日收盘价；
- 当日涨跌幅；
- 行情观察时间；
- 数据来源标识。

腾讯不是公告源、公司新闻源或财联社类市场快讯源，也不是项目最终唯一数据源。当前接入
用于验证真实 Provider 架构、标准存储、错误隔离和长期稳定性观测。

```text
MarketSnapshot ≠ PriceWindow
```

腾讯 `MarketSnapshot.pct_change` 是单点行情中的当日涨跌语义，不代表当前报告所使用的
多日区间收益，不能写入 `PriceWindow.period_pct_change`，也不能直接替换 AkShare
历史行情。

> 腾讯 QuoteProvider 当前处于 Shadow 观测阶段，默认关闭，不参与正式分析和报告生成。

腾讯行情不会进入 Analyzer、Prompt、Report 或通知。完成人工观测并通过单独验收前，
腾讯不能被视为正式行情源或备用行情源。

默认配置保持：

```yaml
storage:
  enabled: false

providers:
  tencent_quote:
    shadow_enabled: false
```

### 单次真实 Shadow 验证

2026-07-14 完成阶段 2-B3-B1 单次受控验证：

| 项目 | 结果 |
|---|---|
| 存储 | 临时 SQLite，验证后清理 |
| 固定证券 | `600519`、`300750`、`000001` |
| 逻辑 `fetch_quotes()` | 1 次 |
| 底层 HTTP 请求 | 1 次，成功 |
| ProviderCall | `provider=tencent-finance`、`operation=quote_shadow` |
| ProviderCall 结果 | `status=success`、`item_count=3`、`retry_count=0` |
| `duration_ms` | 1451 |
| MarketSnapshot | 保存 3 条，source 均为 `tencent-finance` |
| RawResponse | 新增 0 条 |
| 幂等性 | 重复本地保存未产生重复快照 |
| 汇总脚本 | 只读查询通过，查询前后数据库内容未改变 |

本次验证没有使用正式配置或正式数据库，没有加载 LLM、通知和旧 DataSource。验证后临时
数据库、临时配置和运行文件均已清理。README 不保存真实价格、完整请求 URL、原始响应
或 request fingerprint。

一次成功只能证明最小真实链路可用，不能替代多个交易日的稳定性观察。

## 腾讯 Shadow 人工观测计划

下一阶段为“阶段 2-B3-B2：连续 5～7 个交易日人工观测”，当前尚未开始，也尚未完成。
不能用一次运行代替连续观察，也不能在单次 Codex 会话中声称已经完成多日观测。

执行边界：

- 每个交易日只运行一次，推荐在 A 股收盘后执行；
- 使用独立观察数据库，不使用正式数据库；
- 固定选择 3～5 个 CN 标的，不进行循环扫描；
- 不调用真实 LLM，不使用正式通知；
- 不把腾讯数据送入 Analyzer、Prompt、Report 或通知；
- 不自动替换 AkShare，不把腾讯声明为正式或备用行情源；
- 每日执行后保存安全指标记录，不保存原始响应或真实价格。

### 专用观测命令

观测入口默认禁止联网，必须显式提供 `--allow-network`、独立 SQLite 路径和证券代码：

```bash
python scripts/tencent_quote_shadow_observe.py \
  --allow-network \
  --db-path /tmp/tencent_quote_shadow.sqlite3 \
  --symbols 600519 300750 000001
```

推荐固定使用 3～5 个证券，并在每个交易日收盘后最多执行一次。命令不会读取正式
`config.yaml` 或 watchlist，也不会调用旧 DataSource、LLM、报告或通知；当前正式默认
数据库路径会被明确拒绝。未传 `--allow-network` 或证券参数非法时，命令会在创建
Transport 和打开数据库之前失败。

重复执行会复用同一个观察库，并沿用 `security_id + source + observed_at` 的快照幂等
约束。命令只输出安全计数、缺失证券、Issue 标识、`run_id` 和 `provider_call_id`，不输出
原始响应或真实价格。

### 每日观察指标

| 指标 | 目的 |
|---|---|
| ProviderCall success/empty/failed | 接口可用性 |
| `duration_ms` | 调用延迟 |
| `item_count` | 返回完整度 |
| 403/429 | 阻断或限流 |
| timeout/network error | 网络稳定性 |
| Parser issues | 响应协议变化 |
| `observed_at` 新鲜度 | 行情时效 |
| 请求证券缺失数 | 数据完整度 |
| Snapshot 新增数 | 持久化是否正常 |
| 重复快照数 | 幂等表现 |
| `raw_responses` 新增数 | 必须始终为 0 |
| 正式 PipelineRun 状态 | 必须不受腾讯影响 |
| 正式报告是否正常 | 验证业务隔离 |

### 暂停条件

出现以下任一情况时暂停在线观察，先记录安全差异并调查：

- 出现 HTTP 403 或 429；
- 连续两次网络失败；
- 响应协议或字段位置变化；
- Parser 无法识别新状态；
- 多个证券持续缺失；
- `observed_at` 明显陈旧；
- 出现 raw response 非预期写入；
- 正式 PipelineRun 状态受到影响；
- 腾讯数据进入 Analyzer、Prompt、Report 或通知；
- 出现无法解释的重复或覆盖。

### 只读汇总命令

```bash
python scripts/tencent_quote_shadow_summary.py \
  --database <观察数据库路径> \
  --days 7
```

汇总脚本只读 SQLite，不联网、不输出真实价格、不修改数据库、不读取 `.env`，也不调用
LLM 或通知。

观测命令实现完成只表示具备连续人工观测入口，不代表腾讯 QuoteProvider 已完成正式验收
或可以进入正式分析、报告和路由。

### 每日观测记录模板

| 字段 | 记录 |
|---|---|
| 日期 | YYYY-MM-DD |
| 执行时间 |  |
| 请求证券数 |  |
| ProviderCall status |  |
| `item_count` |  |
| `duration_ms` |  |
| 403 / 429 |  |
| timeout / network |  |
| Parser issues |  |
| 缺失证券数 |  |
| `observed_at` 新鲜度 |  |
| Snapshot 新增数 |  |
| `raw_responses` 新增数 | 0 |
| 正式 PipelineRun 状态 |  |
| 正式报告是否正常 |  |
| 备注 |  |

## 长期多数据源路线

项目目标是从多方网站采集可追溯信息，再由标准模型和证据驱动分析组合结果，而不是依赖
腾讯或任何单一网站。不同信息类型应由独立 Provider 承担，不能因为字段外观相似而混合
金融语义。

后续路线包括：

1. 完成腾讯 QuoteProvider 的 5～7 个交易日人工观测并单独验收；
2. 迁移 Profile Provider，保持证券静态资料与动态行情分离；
3. 接入至少两个可切换的 A 股公司新闻来源；
4. 将交易所或巨潮公告建模为独立公告来源；
5. 接入财联社类市场快讯和其他市场资讯来源；
6. 为多源证据增加来源标识、原文链接、时间和质量问题；
7. 在契约、离线测试和在线证据充分后，再设计路由、降级、限流、缓存和增量抓取；
8. 只有通过独立验收的数据，才允许进入 Analyzer、Prompt、Report 或通知。

长期架构要求单一来源失败不能阻断整份日报，来源之间能够交叉验证，报告中的结论能够
追溯到具体证据。腾讯只承担其中一个单点行情 Provider 的候选角色。

## 开发者验证

常规开发验证必须保持离线：

```bash
python -m pytest
python -m compileall -q daily_report_agent tests
python -m daily_report_agent --dry-run
```

开始下一阶段前应再次确认：

- 默认配置仍为 `storage.enabled: false` 和 `shadow_enabled: false`；
- 普通 pytest 和 dry-run 不联网；
- dry-run 不创建数据库；
- Fixture 不包含 API Key、token、Cookie 或未脱敏响应；
- Prompt 和固定 dry-run 哈希保持不变；
- 不修改现有金融判断和报告业务语义；
- 每接入一个 Provider，都有独立契约测试、失败隔离测试和明确的在线门禁。
