# Tencent ProviderRouter Shadow 编排与首次在线验收契约（M1-05A/M1-05B）

> 状态：Implemented — offline assembly and one controlled online acceptance
> 日期：2026-07-31
> 在线状态：新 Router 已完成一次受控在线验收；未进入正式或备用路由

## 1. 结论与边界

M1-05A 将既有腾讯 QuoteProvider 装配到新的 ProviderRouter 和 SQLite Circuit Breaker：

```text
ProviderRegistry
→ SQLite Circuit preflight
→ ProviderRouter
→ TencentQuoteProvider
→ ProviderCall / MarketSnapshot
→ Circuit outcome
→ 继续 legacy 正式日报
```

该链路只属于 `provider_shadow`。它不生成 `StockData` 或 `PriceWindow`，不进入
`AnalysisInput`、Analyzer、Prompt、Report 或 Notifier。Shadow
success/empty/partial/failed/skipped 都不会把正式 Pipeline 标记为 partial，也不会阻断
legacy 报告。

M1-05A 的 Provider、Invoker 和 Transport 验证全部使用 Fake/Fixture，没有发送真实
网络请求。历史 `scripts/tencent_quote_shadow_observe.py` 的 B4 成功是旧 Shadow
入口证据，不是新 Router 证据。M1-05B 随后在精确 M1-05A HEAD 完成新 Router 的首次
单次受控在线验收；两者必须分开表述。

## 2. 五重模式门禁

`provider_shadow` 只有同时满足以下条件才进入编排：

1. `pipeline.data_route=provider_shadow`；
2. `providers.tencent_quote.shadow_enabled=true`；
3. CLI 显式提供 `--allow-provider-shadow`；
4. 非 dry-run；
5. `pipeline.provider_shadow_database_path` 显式指向与正式库不同的普通文件路径。

此外，`max_provider_calls` 必须为 1，候选优先级若显式配置只能包含
`tencent-finance`。空路径、非法类型、目录、与正式库相同的路径或其他 Provider 候选均
安全拒绝。

门禁发生在 `.env`、数据库、在线 Transport、LLM、DataSource 和报告之前。缺少任一条件
不会静默退回 legacy，也不会创建文件。`legacy` 不构造 Registry、Router 或 Circuit
Store，不打开 Shadow 数据库，不产生 Shadow 指标；`provider_primary` 始终在副作用前
拒绝；dry-run 永不运行 Shadow。

默认配置保持：

```yaml
pipeline:
  data_route: legacy
  max_provider_calls: 1
  provider_shadow_database_path: null

storage:
  enabled: false

providers:
  tencent_quote:
    shadow_enabled: false
```

## 3. Registry、Router 与调用预算

Registry 只包含一个注册项：

| 字段 | 值 |
|---|---|
| provider_id | `tencent-finance`（保持既有 Descriptor，不改名） |
| capability | `QUOTE` |
| market | `cn` |
| runtime_stage | `shadow_eligible` |
| enabled | `true` |

RoutePolicy 固定为 `provider_shadow`、候选只有腾讯、`max_call_budget=1`、
`allow_legacy_fallback=false`、`fallback_on_empty=false`。RetryPolicy 固定
`max_attempts_per_provider=1`、`allow_fallback_after_retry=false`。

因此一次运行最多产生：

- 一个候选级 `RouteAttempt`；
- 一个物理调用级 `RetryAttempt`；
- 一个 Invoker 调用；
- 一个 Transport 请求批次。

M1-05A 没有 sleep、Retry、退避、抖动、Retry-After、第二 Provider、网络 Fallback 或
并发。输入还受硬上限 5 个证券约束，使未来惰性在线 factory 的单批次不超过既有腾讯
Transport 上限。

## 4. Circuit Breaker 顺序

Circuit key 固定为：

```text
provider_id=tencent-finance
operation=fetch_quotes
```

顺序和结果：

- CLOSED `allow`：进入一次 Router；
- OPEN `skip`：不构造 Provider、不进入 Invoker、不消耗预算，保存 skipped 审计；
- 到期 OPEN `probe`：SQLite 原子占用唯一 HALF_OPEN probe，再进入一次 Router；
- Provider success/empty/partial：`record_success`，probe 成功后关闭；
- Provider timeout/network/unavailable：按 M1-03 分类 `record_failure`；
- blocked/authentication/protocol：按既有 immediate-open 策略记录；
- validation/evaluator/caller：以 neutral origin 记录，不污染 Provider 健康状态；
- preflight 读取/写入失败：fail closed，不调用 Provider；
- outcome 写入或 stale version 冲突：不重试 Provider、不绕过 Circuit、不写成腾讯成功。

`KeyboardInterrupt` 和 `SystemExit` 继续传播。

## 5. 证券输入与结果判定

输入只从 watchlist 选择 `market=cn` 的证券。使用标准 `Security` 和既有腾讯 symbol 映射
校验；按 watchlist 顺序稳定去重，不支持的号段形成安全 validation Issue，US 证券忽略，
单个非法证券不阻断其他合法证券，名称不用于猜测交易所。

纯 evaluator 的规则：

| Provider 边界结果 | Shadow 终态 |
|---|---|
| 全部请求证券返回且无 ERROR Issue | `success` |
| 正常完成但 0 items | `empty` |
| 缺失证券或存在 ERROR Issue | `partial` |
| ProviderError / invoker / evaluator 失败 | `failed` |
| Circuit OPEN、无合法证券或 fail-closed | `skipped` |

缺失证券形成 `missing_requested_symbol` Issue；partial 保留有效 `MarketSnapshot`，但不调用
第二来源。`MarketSnapshot.pct_change` 不会写入 `PriceWindow.period_pct_change`。

## 6. 独立 SQLite 语义

M1-05A 复用现有 0001/0002 schema，不新增 migration：

- 每次 Shadow 执行建立独立 `PipelineRun`；
- allow/probe 路径建立一个 `ProviderCall`；
- OPEN/no-input/fail-closed 使用 `ProviderCall.status=skipped`，不伪造成功；
- Provider 正常 partial 仍是 Provider 边界 `success`，Shadow PipelineRun 为 `partial`；
- Provider failure 使用 `ProviderCall.status=failed`；
- `retry_count=0`；
- `item_count` 等于该次有效 Provider items 数；
- Security 和 MarketSnapshot 复用既有幂等业务键；
- `raw_responses` 始终为 0，所有 Snapshot `raw_response_id` 为 NULL。

只保存 SHA-256 请求指纹、聚合计数和安全错误码，不保存完整 URL、Header、Cookie、Token、
响应正文、异常正文或真实请求日志。Shadow 数据库路径必须与正式数据库不同；Shadow
初始化、单条 Snapshot 或结束记录失败不会改变 legacy 报告。

## 7. 导入与在线隔离

`tencent_provider_shadow` 模块导入和编排器构造不导入
`TencentOnlineQuoteTransport`、storage 实现或 `requests`，也不打开数据库。默认在线
factory 只会在全部模式门禁、Shadow 数据库初始化、Circuit allow/probe 和
ProviderCall 建立之后由 Invoker 惰性调用。

M1-05A 的全部测试注入 Fake Provider/Invoker；普通 pytest 全局阻断 socket 和 urllib。
M1-05B 只在单独授权下通过专用入口使用过一次该惰性在线边界。官方入口固定为：

```bash
python -m scripts.tencent_provider_shadow_once
```

缺少 `--allow-network-once`、非法固定证券、非法 timeout 或不安全数据库路径时，入口在
数据库创建、在线 Transport 构造和 Provider 调用前以退出码 2 拒绝。`--help` 离线显示
并以退出码 0 结束。`python scripts/tencent_provider_shadow_once.py` 不是官方入口。

## 8. M1-05B 首次受控在线验收

### 8.1 固定范围与结果

| 项目 | 验收事实 |
|---|---|
| 日期 | 2026-07-31 |
| 精确 HEAD | `9dab42ea03370e6828f429aa3890d7c0ab3fb724` |
| 固定证券 | `600519`、`300750`、`000001` |
| 逻辑调用 / HTTP 请求 | 1 / 1 |
| Retry / Fallback / 并发 | 0 / 0 / 0 |
| timeout | 10 秒 |
| 返回项目 / 错误 | 3 / 0 |
| PipelineRun | 1，`status=success` |
| ProviderCall | 1，`tencent-finance/fetch_quotes`，success |
| Security / MarketSnapshot | 3 / 3 |
| Circuit | 1，`closed`，`consecutive_failures=0` |
| RawResponse | 0 |
| SQLite 检查 | integrity/foreign key 通过，重复业务键 0，孤儿快照 0 |
| 临时库 SHA-256 | `ff9d427178a7134ef145cf7aeebe972f1ba8a5f7cd6d7954a021ea4793cc88b7` |
| 清理 | 临时数据库、WAL、SHM 已删除；仓库工作区保持干净 |

验收没有读取、记录或输出具体行情值、原始响应、完整请求 URL、Header、Cookie、Token
或未脱敏异常正文。

### 8.2 首次错误入口与唯一在线请求

第一次尝试使用了非官方直接文件入口：

```bash
python scripts/tencent_provider_shadow_once.py
```

它在导入项目包前以 `ModuleNotFoundError` 失败。失败发生在任何业务或网络动作之前：
HTTP 请求、Provider 调用和逻辑调用均为 0，数据库、WAL、SHM 均未创建，因此不计入在线
请求次数。

修正后的授权执行使用 `python -m scripts.tencent_provider_shadow_once`，是 M1-05B
唯一一次真实在线请求。该事实只证明固定范围下的新 Router 单次链路可用，不是生产 SLA、
高可用或正式路由证明。

## 9. 未完成与下一门禁

M1-05A/M1-05B 仍不完成：

- `provider_primary`；
- 腾讯成为正式或备用数据源；
- 真实 Retry、第二来源 Fallback、限流、缓存或后台恢复；
- Shadow 数据进入分析、Prompt、报告或通知；
- Eastmoney/CNInfo 候选；
- 七个交易日新 Router Shadow；
- Eastmoney News 或 CNInfo 的在线成功证据；
- observed Fixture 或历史 B4 证据变更。

下一步先完成远端 CI/PR 门禁，再单独规划七个交易日 `provider_shadow`。不得把 M1-05B
单次成功描述为生产 SLA、正式路由、高可用完成或七日 Shadow 已开始。
