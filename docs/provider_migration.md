# Provider 迁移边界（阶段 2-A）

> 状态：本文件保留阶段 2-A 至 2-B4 的迁移事实、字段语义和历史门禁。当前生产结构仍未
> 被新 Provider 接管；P1-04B 后的活跃迁移顺序见
> [`production_pipeline_and_evidence_optimization_plan.md`](production_pipeline_and_evidence_optimization_plan.md)。

## 当前生产结构

生产链路保持为：

```text
main.py
→ get_source()
→ CNDataSource / USDataSource
→ StockData
→ stockdata_to_analysis_input()
→ Analyzer
→ Report
```

`daily_report_agent/providers/` 在 2-A 只是旁路契约。`main.py` 没有导入或编排新
Provider，`LegacyDataSourceFacade` 也只供离线迁移测试使用。

## 当前能力拆分

### CNDataSource

现有 `daily_report_agent/datasource/cn.py` 是一个混合数据源：

- `CNDataSource.fetch()`（第 20～32 行）创建并组装 `StockData`，确定日期窗口，依次调度行情、简介和新闻，并聚合“无有效数据”错误。
- `_fetch_prices()`（第 34～48 行）调用 AkShare 历史行情，读取区间首尾收盘价并计算多日区间涨跌；异常文本写入旧 `StockData.error`。
- `_fetch_intro()`（第 50～62 行）读取个股资料，补证券名称并拼装旧 `intro` 文本。该文本当前还包含总市值等随时间变化的数据，不能直接视为新的 `SecurityProfile`。
- `_fetch_news()`（第 64～79 行）读取个股新闻、截断摘要并组装旧新闻对象，同时沿用旧错误聚合规则。
- 上述方法直接进行旧格式解析和字段标准化；真正到标准模型的转换仍由 `ingestion/adapters.py` 中的 `stockdata_to_collection()` 完成。

本阶段不移动、不改写这些职责，也未实例化任何真实 CN Provider Descriptor。

### USDataSource

现有 `daily_report_agent/datasource/us.py` 同样是混合数据源：

- `USDataSource.fetch()`（第 20～33 行）创建和组装 `StockData`，调度三类能力，并把未被内部处理的异常聚合进旧 `error` 字段。
- `_fetch_prices()`（第 35～46 行）使用 yfinance 历史行情，读取区间首尾收盘价并计算多日区间涨跌。
- `_fetch_intro()`（第 48～60 行）使用 Finnhub profile，补名称、行业、交易所、币种和市值，并拼成旧 `intro` 文本。
- `_fetch_news()`（第 62～76 行）使用 Finnhub company news，排序、过滤营销内容、限制数量并组装旧新闻对象。
- 日期、数值、新闻文本与错误仍按旧链路规则处理，标准模型转换继续由既有 adapter 负责。

本阶段不移动、不改写这些职责，也未实例化 yfinance 或 Finnhub 的新 Provider Descriptor。

## 阶段 2-A 的边界

已建立同步的 `QuoteProvider`、`NewsProvider`、`ProfileProvider` Protocol，稳定的
`ProviderDescriptor`/`ProviderResult` 契约，安全 Provider 错误层和独立
`SecurityProfile` 模型。旁路 `LegacyDataSourceFacade` 的方向固定为：

```text
旧 DataSource → StockData → stockdata_to_collection() → CollectedSecurityData
```

以下内容仍被冻结：

- 真实 Provider 尚未实现；
- Legacy Façade 尚未进入生产；
- `provider_calls` 不产生记录；
- `config.yaml` 尚未增加 Provider 配置；
- `main.py` 与生产编排未改变；
- SQLite schema 未改变；
- 不存在从新 Provider 反向转换为 `StockData` 的适配器。

Legacy Descriptor 使用 `legacy-cn-datasource` 或 `legacy-us-datasource`，明确表示整条
混合旧链路，不能冒充 AkShare、yfinance 或 Finnhub 的精确调用来源。

## 金融语义冻结

```text
MarketSnapshot ≠ PriceWindow
```

`QuoteProvider` 只返回某个观察时刻的单点 `MarketSnapshot`。当前旧 DataSource 则读取
一个日期区间的首尾价格并计算区间涨跌，适配后必须继续位于
`CollectedSecurityData.price_window`。Legacy Façade 不生成快照，输出的
`market_snapshots` 保持空元组。

因此，未来腾讯实时行情的单点价格和当日涨跌不能直接替换当前报告中的多日区间涨跌，
也不能把 `MarketSnapshot.pct_change` 填入 `PriceWindow.period_pct_change`。历史行情能力
需要在后续阶段另行设计，不能塞入当前 Quote Protocol。

## 阶段 2-A 当时确定的迁移顺序

1. 2-B1：腾讯 `QuoteProvider` 离线实现与契约测试（已完成）。
2. 2-B2：腾讯 `QuoteProvider` 受控在线冒烟。
3. 2-C：Profile Provider 迁移。
4. 2-D：News Provider 迁移。
5. 2-E：Provider 编排、降级和 `provider_calls` 接入。

在 2-E 之前不允许伪造或写入 `provider_calls`。

上述顺序是阶段 2-A 的历史决策。腾讯 Shadow 后续已经按独立编排写入真实
`provider_calls`，但仍未进入正式业务路由。从 P1-04B 起，项目冻结净新增 Provider，
优先实施新旧双跑、正式路由、高可用、结构化增量分析、Replay 和七日 Shadow；不得把
历史顺序解释为当前仍应先扩充 Provider。

## 阶段 2-B1：腾讯 QuoteProvider 离线状态

`TencentQuoteProvider` 已完成离线结构、代码映射、分批、文本解析、标准异常映射和契约
测试。它仅接受显式注入的 `TencentQuoteTransport`，本阶段没有默认在线 Transport、
endpoint 或正式配置。导入腾讯模块和构造 Provider 都不会联网，只有调用
`fetch_quotes()` 才会调用已注入的 Transport。

当前边界保持不变：

- 尚未进行在线冒烟；
- 尚未进入 `main.py`、DataSource、pipeline、Analyzer 或报告；
- 尚未进入降级路由；
- 尚未记录 `provider_calls`；
- 尚未替换 AkShare；
- 尚未改变报告使用的多日 `PriceWindow`；
- 腾讯结果只表示单点 `MarketSnapshot`。

### 支持的证券代码

- 上海：`600`、`601`、`603`、`605`、`688` 开头的六位代码，映射为 `shXXXXXX`；
- 深圳：`000`、`001`、`002`、`003`、`300`、`301` 开头的六位代码，映射为 `szXXXXXX`；
- 北交所及其他未经确认的号段显式拒绝，不按名称或未验证规则猜测交易所。

### 腾讯字段映射

| 腾讯文本位置 | 标准字段 | 单位 | 缺失处理 | 可信状态 |
|---:|---|---|---|---|
| 2 | `symbol` | 无 | 记录无效 | 已固定，并与响应 `sh/sz` 前缀交叉校验 |
| 3 | `price` | CNY/股 | `None` | 已固定 |
| 4 | `previous_close` | CNY/股 | `None` | 已固定 |
| 30 | `observed_at` | Asia/Shanghai | 使用 aware `fetched_at` 并产生 warning | 已固定 |
| 32 | `pct_change` | 百分比数值 | `None` | 已固定，真实 `0.00` 保留为 `0.0` |
| 成交量位置 | `volume` | 手/股口径不稳定 | `None` | 暂不映射 |
| 成交额位置 | `amount` | 缩放单位不稳定 | `None` | 暂不映射 |
| 换手率位置 | `turnover` | 公开协议口径待确认 | `None` | 暂不映射 |
| PE 位置 | `pe_ttm` | 动态/TTM 口径待确认 | `None` | 暂不映射 |
| PB 位置 | `pb` | 位置契约待确认 | `None` | 暂不映射 |
| 市值位置 | `market_cap` | 缩放单位待确认 | `None` | 暂不映射 |

固定 Fixture 位于 `tests/fixtures/providers/tencent/`，只用于解析契约，不会在线刷新。

## 阶段 2-B2 在线协议差异记录

2026-07-14（Asia/Shanghai）的首次受控请求返回 HTTP 200、GBK 文本，共 3 条记录且
每条 88 个字段。响应顺序与请求一致：`sh600519,sz000001,sz300750`。原 Parser 只解析
出 `sh600519`，另外两条产生 `malformed_record`。结合腾讯当前公开响应样本，差异定位为
完整行情记录首字段存在已观察到的 `51` 变体，而 2-B1 Parser 只允许 `1`。修复前先以
独立失败测试固定该差异；不自动覆盖既有 Fixture，也不保存在线原始响应。

### 2-B2 实施状态

在线 Transport 使用 Python 标准库 `urllib`，endpoint 固定为
`https://qt.gtimg.cn/`，查询通过 `urlencode()` 的 `q` 参数构造。该形式由腾讯当前公开
响应和[腾讯云开发者社区近期示例](https://cloud.tencent.com/developer/article/2509319)
交叉确认。Transport 不接受任意 endpoint，只允许 HTTPS `qt.gtimg.cn`，且不会跟随到
非腾讯域名。

在线能力只通过 `scripts/tencent_quote_smoke.py --allow-network` 显式启用。没有该参数时
返回非零；脚本最多允许 3 个明确证券、单批、无自动重试、无并发，不读取正式配置、
`.env` 或 watchlist，也不访问数据库、LLM、通知和 `provider_calls`。

Transport 明确处理：

- Header 声明的 GBK/GB2312/GB18030/UTF-8；缺少 charset 时固定回退 GB18030；
- 解码失败和超出 128 KiB 的响应；
- timeout、连接/DNS、403、429、其他 HTTP 错误；
- 最终 URL 和重定向目标域名；
- 固定安全 message，底层原因只通过异常链保留。

导入腾讯模块、导入在线 Transport、构造 Transport 或 Provider 均不会联网；只有调用
`fetch_quotes()` 才执行请求。

### 受控在线验证结果

2026-07-14（Asia/Shanghai）对 `600519,000001,300750` 进行了初始验证和修复后复核，
每次均为一个串行批次，无自动重试。最终复核结果：

| 项目 | 结果 |
|---|---|
| HTTP | 200 |
| Content-Type | `text/html; charset=GBK` |
| 解码 | GBK |
| 正文字节数 | 1591 |
| 记录数 | 3 |
| 响应顺序 | `sh600519,sz000001,sz300750` |
| 首字段状态 | `1,51,51` |
| 每条字段数 | `88,88,88` |
| Parser 输出 | 3 个 `MarketSnapshot`，0 issues |
| 403 / 429 / 重定向 | 均未发生 |

首次验证暴露 `51` 状态变体后，先增加失败测试，再将 Parser 的已确认完整记录状态扩展
为 `{1, 51}`，并新增手工脱敏的 `quote_status_51.txt`。没有自动保存或覆盖在线响应。

### 字段语义与 Fixture 对比

| 场景 | 2-B1 Fixture | 在线响应 | 结论 |
|---|---|---|---|
| 记录前缀 | `v_sh` / `v_sz` | `v_sh` / `v_sz` | 一致 |
| 记录分隔 | 分号 | 分号 | 一致 |
| 首字段状态 | `1` | `1` 和 `51` | 已补充变体 Fixture 和 Parser 测试 |
| `symbol` 位置 | 2 | 2 | 已确认 |
| `price` 位置 | 3 | 3 | 已确认 |
| `previous_close` 位置 | 4 | 4 | 已确认 |
| `observed_at` 位置 | 30 | 30 | 已确认，格式 `YYYYMMDDHHMMSS` |
| `pct_change` 位置 | 32 | 32 | 已确认，三个标的交叉计算均一致 |
| 编码 | Fixture 为 UTF-8 测试文件 | HTTP 正文为 GBK | Transport 显式解码后交给同一 Parser |
| 字段数 | 最小样本多为 49 | 88 | Parser 只读取已确认位置；新增 88 字段状态变体样本 |

成交量、成交额、换手率、PE、PB 和市值虽然出现在完整响应中，单位、缩放和口径尚未
完成独立证据验证，因此继续保持 `None`，本阶段不扩展 Parser。

## 阶段 2-B3-A：默认关闭的 Shadow 编排

腾讯 QuoteProvider 已进入正式运行环境的独立 Shadow 旁路，但配置默认关闭。只有非
dry-run、`providers.tencent_quote.shadow_enabled=true`、storage 正常开启且 watchlist
包含 CN 标的时才执行。dry-run、storage 关闭和纯 US watchlist 均不会加载在线
Transport，也不会创建 ProviderCall。

Shadow 在正式 `pipeline_run` 建立后执行，结果只写入现有 `provider_calls`、
`securities` 和 `market_snapshots`。一条 ProviderCall 表示一次逻辑
`TencentQuoteProvider.fetch_quotes()`，`retry_count` 固定为 0，fingerprint 是将
`market:symbol` 排序并以换行连接后的 SHA-256。它不包含名称、价格、URL、路径或时间。
Provider 使用配置的逻辑 `batch_size`；若单个逻辑批次超过阶段 2-B2 Transport 的 5 个
代码安全上限，则按最多 5 个代码顺序拆成必要的底层请求，不并发、不重试。因此一条
ProviderCall 不等同于一个 HTTP 请求。

`provider_calls.status` 表示逻辑 Provider 调用是否产生可用结果：有快照为 `success`，
无异常但无快照为 `empty`，ProviderError 为 `failed`。有效快照同时伴随 ERROR issue
或单条存储失败时，ProviderCall 仍为 `success`，更细的 ShadowResult 为 `partial`。
该状态不是正式 `pipeline_runs.status`；腾讯的 failed、empty 或 partial 均不会改变
日报状态、退出码、Analyzer、Prompt、Report 或通知。

快照继续保持单点 `MarketSnapshot` 语义，按
`security_id + source + observed_at` 幂等保存；不生成 PriceWindow，不覆盖 AkShare 或
legacy 数据，不保存 raw response。只读历史汇总可运行：

```bash
python scripts/tencent_quote_shadow_summary.py --database daily_report_agent/data/agent.db --days 7
```

该工具以 SQLite URI 只读模式工作，不运行 migration、不联网、不输出价格。

## 阶段 2-B4：五日 Shadow 观测验收

2026-07-20 至 2026-07-24 已在冻结观测提交上完成五个连续交易日的受控 Shadow 观测。
只读验收结果为 5 条 PipelineRun、5 条 ProviderCall、15 条 MarketSnapshot 和 0 条
RawResponse；五次调用均为 `success`、`item_count=3`、`retry_count=0`。逐日耗时为
1447、4083、4540、2003、1546 ms，平均 2723.80 ms，最大 4540 ms。

五日证据通过 B4 数据质量验收，但只覆盖固定 3 个证券、每日一次、无并发、无自动重试的
低频受控条件，不构成生产 SLA。腾讯仍是默认关闭的 Shadow Provider，不是正式或备用
行情源，也未通过正式路由、降级、缓存、限流、重试或熔断验收。

完整证据与限制见
[`tencent_quote_b4_acceptance.md`](tencent_quote_b4_acceptance.md)。候选
`b921a8e8a541551e19af069666d9be3edba2fa3d` 已在 Python 3.10/3.13 完成完整离线回归，
并于 2026-07-25 在单独授权后通过同候选单次受控在线验证。该结果只关闭 B4 合并候选
门禁，不表示腾讯已成为正式或备用行情源；正式路由、降级、缓存、限流、重试和熔断仍须
另立任务设计与验收。
