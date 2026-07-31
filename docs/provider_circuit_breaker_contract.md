# Provider Circuit Breaker 状态、SQLite 与 Shadow 契约（M1-04A 至 M1-05B）

> 状态：Implemented — offline transitions, SQLite persistence, Shadow assembly and one acceptance
> 日期：2026-07-31
> 范围：不可变模型、确定性三态转换、SQLite CAS、显式时间、跨连接单探针及腾讯
> Shadow 调用顺序

## 1. 范围与结论

M1-04A 建立独立、无 I/O 的 Circuit Breaker 状态内核，熔断粒度固定为：

```text
provider_id + operation
```

它不读取配置、环境变量、系统时钟或数据库，不 sleep、不联网，也不构造或调用 Provider。
所有转换只依赖不可变输入和调用方显式传入的 timezone-aware `now`。

M1-04B 在该内核外增加 SQLite migration、Repository/Store、version/CAS 和
`BEGIN IMMEDIATE` 原子事务。跨连接单探针已用临时文件数据库离线验证。M1-05A 已将该
Store 接入腾讯 Quote 的 `provider_shadow` 纯离线编排；所有测试只使用 Fake/Fixture，
没有发送网络请求。M1-05B 随后在精确 M1-05A HEAD 完成一次新 Router 受控在线验收，
验收后 Circuit 为 closed、`consecutive_failures=0`。`provider_primary` 仍在业务副作用
前拒绝，默认正式链路仍是 `legacy`。

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
- M1-04A 中的 `half_open_probe_active` 只是纯函数状态；M1-04B 通过 SQLite 原子事务持久化
  该预留；
- 第一版 `half_open_max_probes` 固定为 `1`；
- 不使用全局线程锁、进程内缓存、sleep 或随机延迟保证探针唯一性。

## 6. M1-04B SQLite 持久化

### 6.1 Migration 与行模型

`0002_provider_circuit_breakers.sql` 新增以 `(provider_id, operation)` 为主键的
`provider_circuit_breakers` 表，保存 M1-04A Snapshot 的全部字段，并额外保存：

- `version`：非负 CAS 版本，初始为 `0`；
- `updated_at`：由调用方显式 `now` 产生的 UTC 时间；
- `idx_provider_circuit_breakers_state_open_until`：状态与到期窗口索引。

Migration 不修改 0001；新数据库一次升级到 0002，现有 0001 数据库可无损升级，重复
`initialize()` 幂等。SQL CHECK 固定状态、非负计数、0/1 probe、非负 version 和非空键；
完整状态一致性仍由反序列化构造 `CircuitBreakerSnapshot` 再次验证，损坏行不会被修复。

### 6.2 Repository 与 Store

`CircuitBreakerRepository` 接收显式 `sqlite3.Connection`，只负责：

- `load(key)`；
- `create_closed(key, now=...)`；
- `compare_and_swap(expected, after, updated_at=...)`；
- Snapshot/UTC 时间序列化与损坏行拒绝。

Repository 不 commit。`PersistedCircuitBreakerSnapshot` 将 Snapshot 与 version 分离，
version 不进入 M1-04A 状态转换。

`SQLiteCircuitBreakerStore` 接收 `Database`，提供：

- `load()` / `load_or_create_closed()`；
- `preflight(key, policy, now=...)`；
- `record_success(..., expected_version, now=...)`；
- `record_failure(..., expected_version, now=...)`；
- `record_outcome(..., expected_version, now=...)`。

每次状态写入执行 `WHERE provider_id=? AND operation=? AND version=?`，成功后 version 加一；
影响零行时返回固定 `circuit_state_conflict` 或 `circuit_state_not_found`，不会覆盖新状态。

### 6.3 原子 preflight

到期 OPEN 的流程位于单一 `BEGIN IMMEDIATE` 事务：

```text
取得 SQLite reserved lock
→ 读取最新 Snapshot + version
→ M1-04A evaluate_circuit()
→ OPEN 转 HALF_OPEN active probe
→ CAS 写入 version + 1
→ commit
```

第二个连接只能在首个事务提交后读取；它看到 HALF_OPEN active probe 后返回 skip，且不更新
version。独立连接竞争测试固定为一个 probe、一个 skip，最终只有一行 HALF_OPEN active
状态。正确性不依赖 sleep、随机退避或进程内锁。

## 7. M1-05A Shadow 编排顺序

腾讯 Shadow 已落实：

```text
SQLite Circuit preflight
→ allow / skip / probe
→ 单候选 Router / 最多一次 Invoker
→ record outcome
→ ProviderCall / Snapshot / Shadow PipelineRun
→ legacy 正式日报
```

OPEN 窗口内的 skip 必须发生在 Invoker 和物理调用预算扣减前，因此不调用 Provider、
不消耗预算。allow/probe 后的 Provider success/empty/partial 记录 success 并使 probe
关闭；timeout/network/unavailable 等 Provider 错误按既有分类记录 failure；validation、
evaluator 和 caller 错误为 neutral。Store preflight 失败时 fail closed；outcome 写入或
version 冲突不会触发第二次 Provider 调用。

## 8. 未完成边界

- `provider_primary` 正式编排；
- 新 Router 的生产运行、七个交易日 Shadow、恢复调度、sleep、退避或后台任务；
- 真实 Retry 或第二 Provider Fallback；
- 限流、缓存、freshness 和生产观测；
- 默认配置或正式 DataSource、Analyzer、Report、Notifier 变更。
- 跨主机共享数据库或 SQLite 之外的分布式协调。

M1-05A 本身不授权在线取数。M1-05B 已在 2026-07-31 单独授权下完成一次固定 3 证券、
单逻辑调用、单 HTTP 请求、Retry/Fallback/并发均为 0 的受控在线验证；这只证明单次
Circuit/Router/持久化链路成功，不构成生产 SLA、真实恢复或高可用验收。
