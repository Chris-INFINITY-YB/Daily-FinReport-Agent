# daily_report_agent 开发进度

更新时间：2026-07-24
当前节点：阶段 2-B4 五个连续交易日观测已经完成且证据通过数据质量验收，正在等待合并
候选门禁；腾讯仍是默认关闭的 Shadow Provider，未进入正式或备用行情路由。并行 P1 的
Eastmoney CN Profile 离线骨架和验收入口已完成，并行 P2 的 Eastmoney CN News 离线
Provider、synthetic 契约测试和存储幂等验证已完成。两者的真实脱敏 observed Fixture
仍待补充，均未进入正式数据链路。

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
| 阶段 2-B3-B2：Shadow 专用观测入口 | 已完成 | 独立 CLI、安全联网门禁、独立 SQLite 和离线测试 |
| 阶段 2-B3-B3：单次受控真实观测 | 已完成 | 固定 3 个证券完成一次真实请求和只读验收 |
| 阶段 2-B4：连续人工观察 | 五日证据通过 | 2026-07-20 至 2026-07-24 完成五个交易日观测，等待合并候选门禁 |
| 并行 P1：Eastmoney CN Profile | 阶段性完成 | 离线契约、Provider 骨架和 synthetic 验收入口已完成；真实脱敏 Fixture 未完成 |
| 并行 P2：Eastmoney CN News | 阶段性完成 | 离线 Transport/Parser/Provider、synthetic 契约测试和存储幂等验证已完成；observed Fixture 未完成 |

### 当前基线

```text
腾讯 Shadow 实现基线提交：
98229c67e1a33f83260684b081c7097d92c76c03

腾讯 Shadow 冻结基线测试：
282 passed

当前开发分支测试：
403 passed

已验证 Python 环境：
Python 3.10.20、Python 3.13.9
```

回归哈希：

```text
完整数据 Prompt SHA-256:
7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3

固定日期 dry-run SHA-256:
8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0
```

这两个哈希分别用于发现 Prompt 业务语义和固定 dry-run 报告输出的意外变化。

### 2026-07-17 开发与验证进度

- 新增 `scripts/tencent_quote_shadow_observe.py`，必须显式提供 `--allow-network`、独立
  SQLite 路径和证券代码；未授权联网时不会创建 Transport、发送请求或打开数据库；
- 新增 `tests/unit/test_tencent_quote_shadow_observe.py`，覆盖安全门禁、证券校验、稳定去重、
  Fixture 成功、部分缺失、Parser/Provider 错误和重复执行幂等语义；
- 完整离线测试为 282 项通过，`git diff --check` 通过；本阶段使用
  `PYTHONDONTWRITEBYTECODE=1` 和 `-p no:cacheprovider` 避免测试生成额外缓存文件；
- 专用观测入口已提交为 `98229c67e1a33f83260684b081c7097d92c76c03`，正式配置、
  AkShare 路由、LLM、报告和通知均未修改；
- 2026-07-17 完成一次 B3-B3 受控真实观察：固定请求 3 个证券，返回 3 个证券，逻辑
  `fetch_quotes()` 1 次，Transport 基础调用 1 次，ProviderCall 新增 1 条，
  MarketSnapshot 新增 3 条，RawResponse 新增 0 条，DataIssue 0 条，`retry_count=0`；
- 观察库只读验收和汇总通过，未发现缺失证券或重复快照，汇总前后主数据库哈希一致；
- 当时已在项目目录之外初始化独立的零业务记录持久观察库。B3-B3 样本保留为单次预检，
  不计入 B4 连续交易日证据；该计划随后已于 2026-07-20 开始并在 2026-07-24 完成。

上述结果只证明专用 Shadow 入口和一次固定样本的真实链路满足当前契约，不代表腾讯
QuoteProvider 已通过正式验收，也不授权进入正式分析、报告、通知或数据源路由。

### 2026-07-20 至 2026-07-24 B4 验收

- 连续五个交易日各完成一次固定 3 证券观测，共形成 5 条 ProviderCall、5 条
  PipelineRun、15 条 MarketSnapshot 和 0 条 RawResponse；
- 五次调用均为 `success`、`item_count=3`、`retry_count=0`，三只证券各有 5 条快照，
  无缺失证券、Data issue、重复业务键或孤立快照；
- 15 条快照的 `price`、`previous_close`、`pct_change` 均非空，货币均为 CNY；
- 五日耗时为 1447、4083、4540、2003、1546 ms，平均 2723.80 ms，最大 4540 ms；
- 五个逐日 `audit_manifest.sha256` 均通过，正式数据库未创建，正式分析、报告、通知和
  路由未被观测入口触发。

完整证据、限制和未解除门禁见
[`docs/tencent_quote_b4_acceptance.md`](docs/tencent_quote_b4_acceptance.md)。B4 五日观测
证据通过不等于腾讯获准进入正式路由；当前仍需在合并候选提交上完成完整回归，以及一次
单独授权的受控在线验证。

## 已完成的基础能力

### 工程与离线运行

- 项目可安装为正式 Python 包并支持 `python -m daily_report_agent`；
- dry-run 无需 API Key，不调用真实 LLM、网络数据源或通知；
- Python 3.10 和 Python 3.13 均纳入验证；
- pytest 默认禁止网络访问，Provider 在线验证使用独立、显式入口；
- `.github/workflows/offline-ci.yml` 在 Python 3.10/3.13 执行 pytest、`compileall`、
  `git diff --check` 和工作区清洁检查，不安装 online extra 或注入 API Key。

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
  [`docs/provider_migration.md`](docs/provider_migration.md)；Eastmoney CN Profile 的字段
  证据和在线边界详见
  [`docs/cn_profile_provider_contract.md`](docs/cn_profile_provider_contract.md)，Eastmoney
  CN News 的字段、时间、哈希和证据边界详见
  [`docs/cn_news_provider_contract.md`](docs/cn_news_provider_contract.md)。

## Eastmoney CN Profile Provider（离线）

当前已实现 Provider ID 为 `eastmoney`、能力为 `PROFILE`、市场为 `cn` 的纯离线骨架。
它通过公开的 `EastmoneyProfileProvider` 返回标准 `ProviderResult[SecurityProfile]`，但
没有默认 Transport、在线 Transport 或正式配置项，也没有接入 `CNDataSource` 和生产路由。

实现边界：

- `transport.py` 只定义同步只读 Protocol，接收六位 CN 证券代码并返回不可变行记录；
- Provider 必须显式注入 Transport，导入和构造不会加载 AkShare、pandas、requests 或联网；
- Parser 只处理传入的 `item/value` 行，当前候选字段仅为 `股票简称` 和 `行业`；
- `symbol`、`market` 来自请求 `Security`，`source` 固定为 `eastmoney`；
- `exchange`、`currency`、`description` 保持 `None`，动态市值和行情字段不会进入 Profile；
- 空行记录是带 `profile_not_found` Issue 的成功空结果，部分资料返回
  `missing_profile_fields` Issue，非空异常结构抛出安全 Provider 错误。

相关路径：

```text
daily_report_agent/providers/eastmoney/       离线 Descriptor、Transport、Parser、Provider
scripts/eastmoney_profile_offline_check.py    显式离线验收入口
tests/fixtures/providers/eastmoney/           synthetic Fixture 及边界说明
tests/unit/providers/eastmoney/               Provider、Parser 和 CLI 离线测试
docs/cn_profile_provider_contract.md          字段证据与在线边界
```

### Synthetic Fixture 离线验收

从仓库根目录执行：

```bash
python scripts/eastmoney_profile_offline_check.py \
  --fixture tests/fixtures/providers/eastmoney/profile_synthetic_minimal.json \
  --symbol 600519 \
  --name SYNTHETIC_SECURITY
```

命令只接受本地普通 JSON 文件，不接受 URL、目录或标准输入；它不读取 `.env`、
`config.yaml`、watchlist 或数据库，也不调用 LLM、Analyzer、Report、Notifier 或网络。
成功输出只包含 Provider ID、执行标志、item/issue 数量、安全 Issue code 和存在的字段名，
不会输出公司名称、行业值或 Fixture 原文。

退出码 `0` 表示 Provider 成功完成，包括部分资料或成功空结果；`1` 表示 Provider、Parser
或内部执行失败；`2` 表示证券输入或 Fixture 被拒绝。

当前示例是人工构造的 synthetic Fixture，不是 2026-07-18 失败请求的响应，不能证明
Eastmoney 真实响应具有 `item/value` 结构，也不能把 `name` 或 `industry` 的字段证据从
E1 提升到 E3。2026-07-18 的一次受控请求在本地 `r.json()` 解析边界失败，没有保存原始
响应或创建 observed Fixture。因此 Eastmoney Profile 仍不得被声明为在线可用或进入正式
路由。

## Eastmoney CN News Provider（离线）

当前已实现 Provider ID 为 `eastmoney`、能力为 `NEWS`、市场为 `cn` 的公司相关新闻
Provider。它只接受显式注入的同步只读 Transport，导入和构造均不联网；当前没有默认或
在线 Transport，也没有正式配置项或生产路由。

已验证的离线能力：

- Transport、Parser 和 Provider 职责分离，Parser 不依赖 AkShare、pandas、网络、文件或
  环境变量；
- `source="eastmoney"`、`source_type="news"`，业务范围仅为 A 股个股公司相关新闻，
  不表示公告、市场快讯或研报；
- 标题和摘要执行确定性文本规范化，未知正文保持 `None`，新闻文本只作为不可信数据处理；
- naive 发布时间按 `Asia/Shanghai` 解释，输出的 `published_at` 和 `fetched_at` 均为
  timezone-aware；
- 当前公开调用边界没有可靠 external ID，因此 `external_id=None`，使用稳定
  `news-content-v1` SHA-256 内容哈希生成 ID 并支持精确去重；
- Provider 负责闭区间时间过滤、发布时间降序、相同时间稳定排序和过滤后的 `limit`；
- 空结果、部分坏记录、缺摘要/URL、非法时间及 Transport 失败具有独立安全语义；
- synthetic Provider 结果已通过现有 `SecurityRepository` 和 `NewsRepository` 写入临时
  SQLite：首次插入 5 条新闻和 5 条关联，重复执行新增 0 条；事务失败完整回滚，
  `raw_responses` 始终为 0。

相关路径：

```text
daily_report_agent/providers/eastmoney/news.py             离线 News Provider
daily_report_agent/providers/eastmoney/news_parser.py      纯 Parser 与稳定内容哈希
daily_report_agent/providers/eastmoney/news_transport.py   同步只读 Transport Protocol
tests/fixtures/providers/eastmoney/news_synthetic_multiple.json
tests/unit/providers/eastmoney/test_eastmoney_news_*.py
tests/integration/storage/test_eastmoney_news_storage.py
docs/cn_news_provider_contract.md
```

新闻 Fixture 是人工构造的 synthetic 数据，只证明离线契约、错误隔离和存储幂等行为。
当前没有 observed Fixture，也没有真实时间格式、字段类型、article code 稳定性、实际返回
数量或在线可用性证据。因此 Eastmoney News 不能被声明为在线可用，不能进入正式
DataSource、Pipeline、Analyzer、Prompt、Report 或通知。

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

> 腾讯 QuoteProvider 已完成 B4 五日观测证据验收，但仍是默认关闭的 Shadow Provider，
> 不参与正式分析和报告生成。

腾讯行情不会进入 Analyzer、Prompt、Report 或通知。B4 证据通过不会自动晋级，腾讯仍
不能被视为正式行情源或备用行情源。

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

## 腾讯 Shadow 连续人工观察结果与边界

“阶段 2-B4：连续人工观察”已于 2026-07-20 至 2026-07-24 完成五个连续交易日证据并
通过只读验收。2026-07-17 的 B3-B3 单次观察只作为预检样本，没有计入 B4。下列命令和
指标保留为已执行观察流程的复核说明，不授权再次运行真实观测。

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
  --db-path /path/to/tencent_shadow_continuous.sqlite3 \
  --symbols 600519 300750 000001
```

推荐固定使用 3～5 个证券，并在每个交易日收盘后最多执行一次。命令不会读取正式
`config.yaml` 或 watchlist，也不会调用旧 DataSource、LLM、报告或通知；当前正式默认
数据库路径会被明确拒绝。未传 `--allow-network` 或证券参数非法时，命令会在创建
Transport 和打开数据库之前失败。

连续观察必须使用项目目录之外的持久独立数据库，每天复用同一路径；不得使用 `/tmp`
临时库承载 B4，不得删除、覆盖或为了重试另建观察库。当天无论成功或失败都不得再次
运行真实观测命令。

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
- 任意一次网络失败、超时或连接异常；
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
  --days 30
```

汇总脚本只读 SQLite，不联网、不输出真实价格、不修改数据库、不读取 `.env`，也不调用
LLM 或通知。使用 30 个日历日窗口可以覆盖可能跨周末的 5～7 个交易日观察周期。

五日观测证据通过只表示 B4 数据质量门禁完成，不代表腾讯 QuoteProvider 已获准进入正式
分析、报告或路由。

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

B4 观测期间并行任务、完成状态和后续合并门禁，详见
[`docs/parallel_development_plan.md`](docs/parallel_development_plan.md)。该计划不改变腾讯
QuoteProvider 的验收门禁。

后续路线包括：

1. 在腾讯合并候选提交上完成完整离线回归和一次单独授权的受控在线验证；
2. 为 Eastmoney Profile 补充真实响应制作的最小脱敏 Fixture 和离线契约证据，继续保持
   证券静态资料与动态行情分离；
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
python -m pip install -e ".[test]"
python -m pytest
python -m compileall -q daily_report_agent scripts tests
python -m daily_report_agent --dry-run
```

Eastmoney Profile 的 synthetic 离线验收以及 Eastmoney News 的 synthetic 契约与存储
测试只需要基础依赖和测试依赖，不需要安装 `.[online]`，也不需要 API Key 或 Provider
配置。

开始下一阶段前应再次确认：

- 默认配置仍为 `storage.enabled: false` 和 `shadow_enabled: false`；
- 普通 pytest 和 dry-run 不联网；
- dry-run 不创建数据库；
- Fixture 不包含 API Key、token、Cookie 或未脱敏响应；
- Prompt 和固定 dry-run 哈希保持不变；
- 不修改现有金融判断和报告业务语义；
- 每接入一个 Provider，都有独立契约测试、失败隔离测试和明确的在线门禁。
