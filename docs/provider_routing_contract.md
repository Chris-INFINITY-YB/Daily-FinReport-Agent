# Provider 路由、Retry、Circuit 与 Shadow 契约（M1-01 至 M1-05A）

> 状态：Implemented — pure offline foundation and Tencent Shadow assembly
> 日期：2026-07-30
> 范围：Registry、ProviderRouter、错误分类、Retry、Circuit Breaker 内核与 SQLite Store、
> 统一预算、配置门禁及腾讯 Quote Shadow 纯离线编排

## 1. 结论

M1-01 建立确定、不可变、无 I/O 的选择契约；M1-02 增加仅供单元测试和纯离线内部入口
显式调用的通用 `ProviderRouter`；M1-03 在 Router 内增加类型驱动错误分类、Provider
内部 Retry 状态机和统一物理调用预算；M1-04A 新增尚未接入 Router 的纯离线 Circuit
Breaker 转换内核；M1-04B 增加 SQLite 原子持久化；M1-05A 将这些能力装配到腾讯 Quote
`provider_shadow` 旁路。正式日报仍只由 legacy 链路生成：

```text
legacy
  → 继续执行既有 DataSource → StockData → Analyzer → Report

provider_shadow
  → 仅在五重门禁通过后运行腾讯 Shadow
  → Shadow 完成后无条件继续 legacy 正式日报
  → Shadow 结果不进入 Analyzer / Prompt / Report / Notifier

provider_primary
  → 在读取 .env、启动存储、构造 LLM、调用 DataSource/Provider 或生成报告前拒绝
```

门禁不会根据 `fallback_to_legacy` 静默退回。这样可以避免配置人员以为新链路已经运行，
实际却得到旧链路报告或半成品报告。

M1-05A 的新编排测试也只执行显式注入的 Fake/Fixture Provider，没有调用 Provider API。
Retry 不执行 sleep、退避、抖动或网络等待。当前没有真实网络 Retry/Fallback、第二候选、
限流、缓存或正式 Provider 编排；新 Router 的首次在线请求属于单独授权的 M1-05B。

## 2. 配置契约

默认配置为：

```yaml
pipeline:
  data_route: legacy
  fallback_to_legacy: true
  max_provider_calls: 1
  provider_priorities: {}
  provider_shadow_database_path: null
```

兼容旧配置：缺少整个 `pipeline` 段或缺少 `data_route` 时均解析为 `legacy`。

封闭字段：

| 字段 | 类型与边界 | 当前语义 |
|---|---|---|
| `data_route` | `legacy/provider_shadow/provider_primary` | legacy 默认；Shadow 受五重门禁；Primary 拒绝 |
| `fallback_to_legacy` | 严格布尔值 | 进入未来 Provider 编排后的策略输入；不绕过当前阶段门禁 |
| `max_provider_calls` | `0..100` 整数，布尔值不视为整数 | 路由调用总预算契约 |
| `provider_priorities` | `provider_id: 0..1000000` 映射 | 按 `(priority, provider_id)` 形成确定候选顺序 |
| `provider_shadow_database_path` | 非空路径字符串或 `null` | Shadow 必须显式设置，且不得与正式库相同 |

未知 `pipeline` 字段、非法模式、字符串布尔值、负数、浮点优先级、非法 Provider ID 和
越界值都会安全拒绝。解析过程不读取环境变量、`.env`、凭据、文件或 Transport。

`storage.enabled=false` 与
`providers.tencent_quote.shadow_enabled=false` 保持不变。

M1-05A 的 `provider_shadow` 还要求显式 `--allow-provider-shadow`、非 dry-run、
`max_provider_calls=1`，并只允许 `tencent-finance` 优先级项。任一条件不满足均在读取
`.env`、打开数据库、构造 Transport、LLM 或报告前拒绝。`legacy` 不构造 Registry、
Router、Circuit Store，不打开 Shadow 数据库，也不产生 Shadow 指标。

## 3. Registry

`ProviderRegistry` 的注册项为不可变 `ProviderRegistration`，包含：

- 现有 `ProviderDescriptor`；
- 单一 `capability`；
- 单一 `market`；
- 非负整数 `priority`；
- 严格布尔 `enabled`；
- `runtime_stage`。

Registry 不修改或复制 `ProviderDescriptor` 的金融语义。注册的 capability 和 market
必须已经由 Descriptor 声明；同一
`provider_id + capability + market` 只能注册一次。

查询顺序固定为：

```text
priority 升序 → provider_id 字典序
```

Registry 的构造、注册、`get()` 和 `query()` 只操作内存元数据，不构造 Provider，不导入
在线 Transport，不读取配置或环境，也不执行网络或持久化。

## 4. 运行阶段隔离

`ProviderRuntimeStage` 为封闭枚举：

| stage | `provider_shadow` | `provider_primary` |
|---|---:|---:|
| `offline_only` | 不可选 | 不可选 |
| `shadow_eligible` | 可选 | 不可选 |
| `production_eligible` | 不可选 | 可选 |

阶段采用精确匹配。生产候选不会因为级别更高而自动进入 Shadow，Shadow 候选也不会进入
生产路由。晋级或降级必须修改显式注册项并单独验收。

当前 Eastmoney Profile/News 和 CNInfo Profile 的离线骨架没有被创建为正式 Registry
注册项。腾讯原有 Shadow 旁路也没有因此升级为正式候选。

## 5. RoutePolicy 与 RouteResult

不可变 `RoutePolicy` 表达：

- `market`
- `capability`
- 显式候选 Provider 顺序；
- 是否允许 legacy fallback；
- 最大 Provider 调用预算；
- 当前路由模式。

`select_route()` 只做纯选择：

1. `legacy` 不查询 Registry；
2. 调用预算为 `0` 时不检查或选择 Provider；
3. 其他 Provider 模式严格按策略中的候选顺序检查注册项；
4. disabled 或 runtime stage 不匹配的注册项被跳过；
5. 未注册或 capability/market 不匹配安全拒绝；
6. 没有合格候选时，根据策略返回 `legacy_fallback` 或 `rejected`。

`RouteResult[T]` 表达：

- 最终选中的 `ProviderRegistration`；
- 路由选择过程中检查过的 Provider ID；
- 是否发生 legacy fallback；
- 封闭终态；
- 安全错误码；
- 可选且必须与所选 Descriptor 一致的标准 `ProviderResult[T]`。

M1-01 的 `select_route()` 不会调用 Provider，因此其 `provider_result` 为 `None`。
`attempted_provider_ids` 在本阶段表示“路由选择尝试”，不表示已经发出网络请求。后续正式
编排必须在真实调用边界重新记录调用顺序和结果，不能把选择尝试伪报为 ProviderCall。

## 6. M1-02 ProviderRouter 状态机

`ProviderRouter[T]` 和便捷入口 `execute_route()` 只接受显式注入：

- `ProviderRegistry`
- `RoutePolicy`
- 同步 `ProviderInvoker[T]`
- 纯 `ResultEvaluator[T]`

构造 Router 不执行 Invoker。执行时严格按 RoutePolicy 的候选顺序同步串行处理，每个
Provider ID 在一次 route 中最多出现一个 `RouteAttempt`；RoutePolicy 会拒绝重复候选。
M1-03 允许该 RouteAttempt 内部记录多个物理调用，但不重复创建候选级 Attempt。

每个候选产生一个不可变 `RouteAttempt`：

| 字段 | 语义 |
|---|---|
| `provider_id` | 安全、规范化 Provider ID |
| `attempt_index` | 从 1 连续递增的候选位置 |
| `terminal_status` | `success/empty/partial/failed/skipped` |
| `item_count` / `issue_count` | 非负标准结果计数 |
| `error_code` | 固定安全短码，不保存异常正文 |
| `selected` | 是否成为最终标准结果 |
| `fallback_triggered` | 本次终态是否实际推进后续候选 |
| `retry_attempts` | 当前候选的零个或多个物理调用审计记录；skipped 必须为空 |

`skipped` 用于 disabled、runtime stage 不匹配、未注册/能力市场不匹配或预算耗尽；它不
调用 Invoker，也不消耗预算。Router 不记录 URL、Header、Cookie、Token、原始响应、
证券价格、新闻正文、数据库路径或异常正文。

### 6.1 状态转换

```text
candidate
→ skipped: 不调用，继续检查候选
→ failed: 预算和候选仍可用时继续 Fallback
→ success: 选择并停止
→ empty: RoutePolicy.fallback_on_empty 决定停止或继续
→ partial: ResultEvaluator 的 FallbackDecision 决定停止或继续
```

Evaluator 只返回 `ResultEvaluation`，通用 Router 不猜测 Quote 缺失证券、News 时间窗口
或其他业务 partial 规则。所有通过 evaluator 的结果均保存在 `retained_results`；
M1-02 没有 ResultMerger，也不进行跨 Provider 数据合并。

### 6.2 调用预算

- `max_call_budget` 是整次 route 的物理 Invoker 总调用预算；
- 预算在进入 Invoker 前扣减，因此 ProviderError 或普通异常不能绕过预算；
- Provider 首次调用、同 Provider Retry、Fallback Provider 首次调用及其 Retry 均消耗 1；
- skipped、evaluator 和只进行错误分类均消耗 0；
- 预算为 0 时所有合格候选都记录为 `skipped/call_budget_exhausted`；
- 预算耗尽后既不得 Retry，也不得执行后续 Fallback Provider；
- `RouteResult` 返回 `call_budget_total/used/remaining`，三者严格满足
  `used + remaining == total`；
- `call_budget_used` 必须等于所有 RouteAttempt 内 `RetryAttempt` 的总数，预算不得为负。

### 6.3 异常边界

- 标准 `ProviderError` 只保留其安全 code；缺失 code 时使用 `provider_failed`；
- 普通异常固定映射为 `invoker_failed`，不保存类型或正文；
- evaluator 异常固定映射为 `evaluator_failed`；
- 非 `ProviderResult`、Descriptor 不一致和非法 evaluator 返回均成为安全 failed；
- `KeyboardInterrupt` 和 `SystemExit` 不被吞掉；
- 只有 RouteAttempt 成功构造并标记 selected 后，Router 才会返回 Provider 成功终态。

## 7. M1-03 错误分类与 Retry

### 7.1 封闭错误分类

`classify_provider_error()` 只检查标准异常类型，不读取异常 message、URL、HTTP 状态正文、
Header、Retry-After 或动态字符串。分类表为：

| 异常类型/边界 | RetryErrorCode | ProviderErrorClass | 默认 Retry |
|---|---|---|---:|
| `ProviderTimeoutError` | `timeout` | `transient` | 是 |
| `ProviderNetworkError` | `network_error` | `transient` | 是 |
| `ProviderUnavailableError` | `provider_unavailable` | `unavailable` | 是 |
| `ProviderRateLimitError` | `rate_limited` | `rate_limited` | 否；显式开关可启用 |
| `ProviderBlockedError` | `blocked` | `blocked` | 否 |
| `ProviderAuthenticationError` | `authentication` | `authentication` | 否 |
| `ProviderValidationError` | `validation` | `validation` | 否 |
| `ProviderParseError` | `protocol` | `protocol` | 否 |
| 其他标准 ProviderError | `provider_failed` | `permanent` | 否 |
| 普通 Exception | `invoker_failed` | `unknown` | 否；显式开关可启用 |
| 非法 ProviderResult | `invalid_provider_result` | `protocol` | 否 |
| evaluator/非法 evaluation | 固定 evaluator 错误码 | `permanent/protocol` | 否 |

现有 `ProviderError.retryable` 字段继续服务既有 Provider/DataIssue 契约；Router 的 Retry
决策不读取该动态布尔值，而以本节封闭类型分类和显式 RetryPolicy 为准。

### 7.2 RetryPolicy

不可变 `RetryPolicy` 表达：

- `max_attempts_per_provider`：`1..100`，包含首次调用，默认 `1`；
- `retryable_error_codes`：只接受封闭 `timeout/network_error/provider_unavailable` 枚举；
- `retry_on_rate_limit`：默认 `false`；
- `retry_on_unknown`：默认 `false`；
- `allow_fallback_after_retry`：默认 `true`。

不传 RetryPolicy 等价于默认策略，因此物理调用行为与 M1-02 相同。字符串布尔值、bool
伪装整数、空/未知错误码、可变集合和越界次数均安全拒绝。

### 7.3 RetryAttempt 与状态转换

每次进入 Invoker 前先消耗预算，并创建一个不可变 `RetryAttempt`。公开字段严格限制为：

- `provider_id`
- 从 1 递增的 `provider_attempt_index`
- 整条 route 从 1 递增的 `global_call_index`
- `terminal_status`
- 封闭 `error_code/error_class`
- 封闭 `retry_decision/retry_reason_code`

状态转换：

```text
RouteAttempt(candidate)
  → physical call success
      → evaluator success/empty/partial
      → success 或按 M1-02 规则 fallback（不做传输 Retry）
  → physical call failed
      → 纯错误分类
      → retry：同一 RouteAttempt 内再次调用
      → fallback：完成当前 RouteAttempt 后进入下一候选
      → stop：结束 Provider 路由
  → budget exhausted
      → 禁止 Retry；后续候选仅记录 skipped/call_budget_exhausted
```

rate limit 默认不重试并优先进入可用的下一候选；只有显式
`retry_on_rate_limit=true` 才允许在次数和全局预算内 Retry。普通未知 Exception 同理。
达到 Provider 次数上限后，只有 `allow_fallback_after_retry=true`、仍有候选且物理预算
充足时才继续 Fallback。

`RetryAttempt` 不包含 URL、Header、Cookie、Token、原始响应、HTTP 正文、Exception
message、新闻正文、股票价格、数据库路径或动态扩展字段。

## 8. 安全错误码

当前封闭错误码：

- `route_stage_not_enabled`
- `duplicate_registration`
- `unknown_provider`
- `capability_market_mismatch`
- `call_budget_exhausted`
- `no_eligible_provider`
- `provider_disabled`
- `runtime_stage_ineligible`
- `provider_failed`
- `invoker_failed`
- `invalid_provider_result`
- `evaluator_failed`
- `invalid_evaluation`

错误消息只描述契约失败，不包含证券、价格、URL、Header、Cookie、Token、响应正文、
数据库路径或底层异常正文。

Retry 审计另使用第 7 节列出的封闭 `RetryErrorCode`，不会把底层动态错误正文变成分类码。

## 9. M1-04A/M1-04B Circuit Breaker 边界

M1-04A/M1-04B 的完整转换和持久化契约见
[`provider_circuit_breaker_contract.md`](provider_circuit_breaker_contract.md)。它复用
M1-03 的 `ProviderErrorClass` 和 `RetryErrorCode`，但保持为独立纯函数模块，未修改
`ProviderRouter` 或调用预算。M1-04B Store 使用 SQLite `BEGIN IMMEDIATE` 与 version/CAS
保证到期 OPEN 的跨连接单探针预留。

未来编排顺序固定为：

```text
Router preflight
→ Circuit Breaker evaluate
→ allow / skip / probe
→ Invoker / Retry
→ record outcome
→ Fallback decision
```

其中 OPEN 窗口内的 `skip` 必须发生在 Invoker 和预算扣减前。M1-05A 已在腾讯 Shadow
编排中落实该顺序；Store 失败时 fail closed，不调用 Provider，Shadow 失败也不改变正式
Pipeline 状态。

## 10. M1-05A Tencent Shadow 编排

M1-05A 只注册
`tencent-finance + QUOTE + cn + shadow_eligible`，策略固定
`max_call_budget=1`、`max_attempts_per_provider=1`、无 legacy fallback、无 empty/partial
第二来源 fallback。单次运行至多产生一个 `RouteAttempt`、一个 `RetryAttempt` 和一次物理
Invoker 调用。

编排顺序固定为：

```text
独立 Shadow PipelineRun
→ SQLite Circuit preflight(tencent-finance, fetch_quotes)
→ skip（预算 0）或 allow/probe
→ 单候选 ProviderRouter
→ Circuit success/failure/neutral outcome
→ ProviderCall / Security / MarketSnapshot
→ 结束 Shadow PipelineRun
→ legacy 正式日报
```

腾讯 evaluator 不进行网络调用，也不把 `MarketSnapshot.pct_change` 转为
`PriceWindow.period_pct_change`。全部请求返回且无 ERROR Issue 为 success；正常零项为
empty；缺失证券或 ERROR Issue 为 partial；ProviderError 为 failed。partial 保留有效
快照，但不会执行第二来源 Fallback。

Shadow SQLite 与正式数据库路径强制隔离，复用现有 0001/0002 schema。Provider 边界的
partial 仍以 `provider_calls.status=success`、Shadow `pipeline_runs.status=partial`
表达，因此不需要扩大 migration；`retry_count=0`，`raw_responses=0`。

完整边界见
[`tencent_provider_shadow_contract.md`](tencent_provider_shadow_contract.md)。

## 11. 未完成边界

下列能力不属于 M1-01 至 M1-05A：

- `provider_primary` 编排；
- 新 Router 的受控在线验证或生产网络调用；
- 部分结果业务合并；
- 真实网络等待、退避、抖动、Retry 或在线 Fallback；
- Circuit Breaker 的生产运行编排；
- 限流、缓存、freshness 和审计持久化；
- 腾讯行情进入 Analyzer/Report/Notifier；
- Eastmoney/CNInfo 在线接入；
- 旧 DataSource 或自由文本 Analyzer 删除。

`provider_shadow` 已具备纯离线装配和严格门禁，但尚未通过 M1-05B 新链路在线验证；
`provider_primary` 继续拒绝。在在线授权和后续迁移验收前，默认配置必须保持 `legacy`，
腾讯不得被描述为正式或备用行情源。
