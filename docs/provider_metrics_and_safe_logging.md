# Provider 指标与安全日志

更新时间：2026-07-27

## 1. 范围与结论

本设计建立纯离线、可注入测试、默认安全的 Provider 终态指标事件，并只在现有腾讯
Quote Shadow 编排边界完成最小接入。它不保存业务数据，不替代
`ProviderCallRepository`，不改变 Provider 返回、持久化或正式日报 Pipeline 状态。

本任务不表示正式 Provider 路由、降级、重试、缓存、限流或熔断已经设计，也不启用新的
在线能力。Eastmoney Profile、Eastmoney News 和其他尚未进入生产编排的 Provider 均未
接入。

## 2. 事件白名单

`ProviderMetricEvent` 是 `frozen=True, slots=True` 的不可变 dataclass。公开字段严格为：

| 顺序 | 字段 | 定义 |
|---:|---|---|
| 1 | `provider_id` | 复用 `normalize_provider_id()` 的稳定 Provider ID |
| 2 | `operation` | 最多 64 字符的小写 ASCII 短标识 |
| 3 | `status` | `ProviderMetricStatus` 封闭枚举 |
| 4 | `duration_ms` | 单调时钟得到的非负整数毫秒 |
| 5 | `item_count` | Provider 返回的标准 item 数量 |
| 6 | `issue_count` | Provider issue 与 Shadow 单项存储失败的聚合数量 |
| 7 | `retry_count` | 本次逻辑调用真实重试次数；腾讯 Shadow 当前固定为 `0` |
| 8 | `error_code` | 最多 64 字符的安全小写短标识，或 `None` |

事件没有扩展字典、自由文本 message、details、tags 或动态键，因此调用方无法通过正常构造
接口夹带额外数据。`duration_ms` 和三个计数只接受非负 `int`，明确拒绝 `bool`。

## 3. 状态与错误码

允许状态只有：

- `success`：返回至少一个可用 item，且没有导致 Shadow 结果降级的错误；
- `empty`：Provider 成功完成，但 item 数量为零；
- `partial`：存在可用 item，但 Provider error issue 或单项 Snapshot 存储失败；
- `failed`：标准 `ProviderError` 或未分类普通异常导致调用失败；
- `skipped`：Shadow 已启用，但前置门禁使 Provider 调用没有执行。

`partial` 是安全指标和 `TencentQuoteShadowResult` 的状态，不改变现有 ProviderCall 语义。
例如 Provider 已成功返回 item、但结果包含 error issue 时，ProviderCall 仍按原契约记录
`success`，指标记录 `partial`。

`error_code` 使用与 Provider 标识相同风格的受控机器标识，只允许小写 ASCII 字母开头，
后续为小写字母、数字、短横线或下划线。URL、查询参数、赋值表达式、点号路径、空白、
大写自由文本和超过 64 字符的值均不能进入事件。标准 `ProviderError.code` 复用同一校验；
没有标准错误码时使用受控常量，例如 `provider_error` 或 `unexpected_error`。

## 4. 明确禁止的数据

事件和由事件生成的日志不得包含：

- 证券代码、证券名称、watchlist 或请求证券数量明细；
- 真实价格、响应业务字段、完整 DataFrame 或 Snapshot 内容；
- Pipeline run ID、数据库路径、ProviderCall ID 或请求指纹；
- URL、查询参数、重定向地址、HTTP Header、Cookie、Token、API Key、Authorization；
- 请求体、响应正文、响应片段或 RawResponse；
- Exception 正文、堆栈、`repr(exception)` 或异常链；
- `DataIssue.message`、新闻标题、摘要或 Profile 字段；
- 自由文本 details、任意附加字典或动态键值。

不对原始异常做日志前字符串替换。原始异常从不进入事件；未分类异常只映射到固定
`unexpected_error`。同理，`DataIssue.message` 可能包含上游或业务内容，因此只聚合
`issue_count`，不记录 message。

## 5. 固定结构化日志

默认输出使用 Python 标准 `logging`，不增加依赖、不创建日志文件。消息是字段顺序固定、
无额外前缀的单行紧凑 JSON，例如：

```json
{"provider_id":"tencent-finance","operation":"quote_shadow","status":"success","duration_ms":125,"item_count":2,"issue_count":0,"retry_count":0,"error_code":null}
```

`format_provider_metric_event()` 只接受已构造成功的 `ProviderMetricEvent`，不接受拥有相似
属性的任意对象。输出器是可注入的 `Callable[[ProviderMetricEvent], None]`，测试不依赖
真实文件、syslog 或外部服务。

## 6. 失败隔离

`emit_provider_metric_safely()` 只捕获普通 `Exception`。指标 Handler、内存替身或其他
输出器的普通失败不会：

- 改变 Provider 调用结果或 `TencentQuoteShadowResult`；
- 把 `success` 改为 `partial` 或 `failed`；
- 改变 ProviderCall 的状态、数量、重试次数或请求指纹；
- 再次调用 Provider、保存 Snapshot 或触发重试；
- 改变正式日报 PipelineRun 状态。

`KeyboardInterrupt`、`SystemExit` 等 `BaseException` 控制流不会被吞掉。输出失败本身不再
写入另一条日志，避免递归失败或泄露输出异常正文。

## 7. 与 ProviderCallRepository 的关系

两者职责不同且不互相替代：

- `ProviderCallRepository` 是可选 SQLite 证据，包含运行关联、请求指纹、开始/结束时间及
  既有持久化错误摘要；
- `ProviderMetricEvent` 是瞬时聚合指标，只包含八个白名单字段，不含 run ID、数据库路径、
  call ID、请求指纹或业务数据；
- 指标输出发生在原有 ProviderCall 结束与 Shadow 结果确定之后；
- 指标输出失败不回写数据库，也不改变 ProviderCall 的既有终态；
- 腾讯 Shadow 的真实重试语义仍为 `retry_count=0`，指标不会伪造重试。

## 8. 当前接入边界

仅 `maybe_run_tencent_quote_shadow()` 和 `run_tencent_quote_shadow()` 接入：

- 每次已进入的逻辑调用最多产生一个终态事件；
- 成功、空结果、部分结果、标准 `ProviderError` 和未分类普通异常均有终态事件；
- storage 未启用、没有合格 CN 证券、store 初始化失败或 ProviderCall 无法建立时，可产生
  一个 `skipped` 事件；
- 默认 `shadow_enabled=false` 和 dry-run 路径不产生日志事件、不构造 Store 或 Provider，
  也不加载在线 Transport；
- 耗时复用现有单调时钟，不引入等待；
- 事件的 `item_count` 与 ProviderCall 的 item 口径一致，不使用 Snapshot 创建数量替代。

腾讯在线 Transport、请求分批、缓存、重试、限流和网络错误映射均未修改。Eastmoney
Provider 没有接入，因为它们尚未进入当前生产编排。纯 Parser 不包含任何指标逻辑。

## 9. 当前限制

- 默认日志只提供安全事件基础设施；部署方若增加 Handler，仍须保证其目标系统、访问控制
  和保留周期符合运行环境要求；
- 事件当前不含时间戳、主机、环境、run ID 或追踪 ID，这是有意的最小安全边界；
- 本设计不提供跨 Provider 聚合、SLA、告警、采样或持久化指标后端；
- 新 Provider 只有在进入明确编排边界后，才能单独设计接入和离线失败隔离测试。
