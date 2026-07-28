# Provider Circuit Breaker 纯离线契约（M1-04A）

> 状态：Implemented — pure offline state transitions
> 日期：2026-07-28
> 范围：不可变模型、确定性三态转换、错误映射、显式时间与单探针契约

## 1. 范围与结论

M1-04A 建立独立、无 I/O 的 Circuit Breaker 状态内核，熔断粒度固定为：

```text
provider_id + operation
```

它不读取配置、环境变量、系统时钟或数据库，不 sleep、不联网，也不构造或调用 Provider。
所有转换只依赖不可变输入和调用方显式传入的 timezone-aware `now`。

本实现尚未接入 `ProviderRouter`，没有 SQLite migration、Repository、锁或跨进程状态
共享，不具备 Cron 多进程安全。`provider_shadow/provider_primary` 仍在业务副作用前
拒绝，默认正式链路仍是 `legacy`。

## 2. 核心模型

### 2.1 CircuitBreakerKey

`CircuitBreakerKey(provider_id, operation)` 是不可变键。两个字段均复用 Provider 契约的
安全规范化函数：trim 后只允许稳定的小写 ASCII 短标识，拒绝空值、非法类型、控制字符、
换行、URL、Token、Cookie、Header 和动态响应内容。键不包含证券代码或业务数据。

### 2.2 CircuitBreakerPolicy

默认策略为：

| 字段 | 默认值 |
|---|---|
| `failure_threshold` | `3` |
| `open_duration` | `5 minutes` |
| `half_open_max_probes` | `1`，第一版不可修改 |
| `counted_error_classes` | `transient/unavailable/rate_limited/permanent/unknown` |
| `immediate_open_error_classes` | `blocked/authentication/protocol` |

阈值只接受 `1..1_000_000` 的整数，布尔值不视为整数；打开时间必须大于零且不超过
365 天；错误集合必须是只含允许的 `ProviderErrorClass` 的 `frozenset`，计数和立即打开
集合不得重叠。

### 2.3 CircuitBreakerSnapshot

不可变快照记录：

- `key`、`state`、`consecutive_failures`；
- `opened_at`、`open_until`、`last_transition_at`；
- `last_failure_at`、`last_success_at`；
- 封闭、安全的 `last_error_code`；
- `half_open_probe_active`。

所有非空时间必须 timezone-aware。`create_closed_circuit(..., now=...)` 创建无打开窗口、
无 active probe、失败计数为零的初始 CLOSED 快照。OPEN/HALF_OPEN 必须有顺序正确的打开
窗口；CLOSED 不得保留窗口或 active probe；失败计数不得为负。快照不保存异常正文或
动态 details。

### 2.4 CircuitTransition

每次转换返回不可变的 `before/after/decision/reason/changed`。决策只允许
`allow/skip/probe`，原因只允许固定 `CircuitTransitionReason`；`changed` 必须与两个
快照是否不同一致。

## 3. 状态转换

| 当前状态/事件 | 决策 | 下一状态 | 核心效果 |
|---|---|---|---|
| CLOSED / preflight | allow | CLOSED | 不变 |
| CLOSED / success | allow | CLOSED | 失败计数归零，记录成功，清除错误码 |
| CLOSED / counted failure，未达阈值 | allow | CLOSED | 失败计数加一 |
| CLOSED / counted failure，达到阈值 | skip | OPEN | 建立新打开窗口 |
| CLOSED / immediate-open failure | skip | OPEN | 首次即打开 |
| OPEN / `now < open_until` | skip | OPEN | 不变 |
| OPEN / `now >= open_until` | probe | HALF_OPEN | 占用唯一探针 |
| HALF_OPEN / active probe 再次 preflight | skip | HALF_OPEN | 不允许第二探针 |
| HALF_OPEN / inactive probe preflight | probe | HALF_OPEN | 占用唯一探针 |
| HALF_OPEN / probe success | allow | CLOSED | 清空窗口、错误码和失败计数 |
| HALF_OPEN / probe failure | skip | OPEN | 从失败时刻重建打开窗口 |
| 任意可记录状态 / neutral | 状态相关 | 原健康状态 | 不计失败、不伪装成功 |

HALF_OPEN 的 neutral 结果会释放当前探针，使后续调用可以重新申请探针，但不会增加失败
计数、打开熔断器或写入成功。OPEN 状态不会接受绕过 preflight 的未授权 Provider 结果。

## 4. 错误分类映射

本模块不建立第二套 Provider 异常分类，也不读取异常正文。它只消费 M1-03 已生成的
`ErrorClassification(error_code, error_class)`：

| M1-03 ProviderErrorClass | 默认 CircuitOutcome |
|---|---|
| `transient` | `counted_failure` |
| `unavailable` | `counted_failure` |
| `rate_limited` | `counted_failure` |
| `permanent` | `counted_failure` |
| `unknown` | `counted_failure` |
| `blocked` | `immediate_open` |
| `authentication` | `immediate_open` |
| `protocol` | `immediate_open` |
| `validation` | `neutral` |

`CircuitOutcomeOrigin` 明确结果边界。只有 `provider` 来源使用上表；
`evaluator/merger/caller/input` 一律为 neutral，避免把非 Provider 故障计入 Provider
健康状态。快照只保存 `RetryErrorCode`，不保存 message、异常类型名、URL 或响应正文。

## 5. 时间与单探针语义

- 每个 public factory/转换函数都要求显式传入 timezone-aware `now`；
- `now < snapshot.last_transition_at` 安全拒绝，不读取本地当前时间修复；
- OPEN 在 `now == open_until` 时即可转为 HALF_OPEN；
- HALF_OPEN active probe 的第二次检查返回 skip；
- `half_open_probe_active` 只是纯函数状态契约，不是线程锁或进程锁；
- 第一版 `half_open_max_probes` 固定为 `1`；
- M1-04B 必须通过 SQLite 原子操作实现跨进程探针预留和状态持久化。

## 6. 未来编排顺序

未来集成必须保持：

```text
Router preflight
→ Circuit Breaker evaluate
→ allow / skip / probe
→ Invoker / Retry
→ record outcome
→ Fallback decision
```

OPEN 窗口内的 skip 必须发生在 Invoker 和物理调用预算扣减前，因此不调用 Provider、
不消耗预算。M1-04A 未修改 `ProviderRouter`、Retry 或预算实现，只固定未来契约。

## 7. 未完成边界

- SQLite schema、migration、Repository 和原子状态更新；
- 跨进程状态共享、锁和真正的 half-open 原子探针；
- Router、Retry、Fallback 或正式/Shadow 编排集成；
- Provider 网络调用、恢复调度、sleep、退避或后台任务；
- 限流、缓存、freshness 和生产观测；
- 默认配置或正式 DataSource、Analyzer、Report、Notifier 变更。
