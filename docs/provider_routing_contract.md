# Provider 路由基础契约（M1-01）

> 状态：Implemented — pure offline foundation
> 日期：2026-07-28
> 范围：Registry、RoutePolicy、RouteResult、配置解析和正式模式门禁

## 1. 结论

M1-01 只建立确定、不可变、无 I/O 的路由基础，不接管正式日报主链路。当前唯一可执行
模式仍是 `legacy`：

```text
legacy
  → 继续执行既有 DataSource → StockData → Analyzer → Report

provider_shadow
provider_primary
  → 配置可以解析
  → 在读取 .env、启动存储、构造 LLM、调用 DataSource/Provider 或生成报告前
     抛出 route_stage_not_enabled
```

门禁不会根据 `fallback_to_legacy` 静默退回。这样可以避免配置人员以为新链路已经运行，
实际却得到旧链路报告或半成品报告。

本任务没有调用 Provider API、创建 observed Fixture、实现在线 Transport、Retry、
网络 Fallback、Circuit Breaker、限流、缓存或正式 Provider 编排。

## 2. 配置契约

默认配置为：

```yaml
pipeline:
  data_route: legacy
  fallback_to_legacy: true
  max_provider_calls: 1
  provider_priorities: {}
```

兼容旧配置：缺少整个 `pipeline` 段或缺少 `data_route` 时均解析为 `legacy`。

封闭字段：

| 字段 | 类型与边界 | 当前语义 |
|---|---|---|
| `data_route` | `legacy/provider_shadow/provider_primary` | 只有 `legacy` 可执行 |
| `fallback_to_legacy` | 严格布尔值 | 进入未来 Provider 编排后的策略输入；不绕过当前阶段门禁 |
| `max_provider_calls` | `0..100` 整数，布尔值不视为整数 | 路由调用总预算契约 |
| `provider_priorities` | `provider_id: 0..1000000` 映射 | 按 `(priority, provider_id)` 形成确定候选顺序 |

未知 `pipeline` 字段、非法模式、字符串布尔值、负数、浮点优先级、非法 Provider ID 和
越界值都会安全拒绝。解析过程不读取环境变量、`.env`、凭据、文件或 Transport。

`storage.enabled=false` 与
`providers.tencent_quote.shadow_enabled=false` 保持不变。

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

M1-01 不会调用 Provider，因此 `select_route()` 返回的 `provider_result` 为 `None`。
`attempted_provider_ids` 在本阶段表示“路由选择尝试”，不表示已经发出网络请求。后续正式
编排必须在真实调用边界重新记录调用顺序和结果，不能把选择尝试伪报为 ProviderCall。

## 6. 安全错误码

当前封闭错误码：

- `route_stage_not_enabled`
- `duplicate_registration`
- `unknown_provider`
- `capability_market_mismatch`
- `call_budget_exhausted`
- `no_eligible_provider`

错误消息只描述契约失败，不包含证券、价格、URL、Header、Cookie、Token、响应正文、
数据库路径或底层异常正文。

## 7. 未完成边界

下列能力不属于 M1-01：

- Provider 实例构造和真实调用；
- Provider Shadow/Primary 编排；
- 部分结果合并；
- Retry 和网络 Fallback；
- Circuit Breaker、限流、缓存、freshness 和审计持久化；
- 腾讯行情进入 Analyzer/Report/Notifier；
- Eastmoney/CNInfo 在线接入；
- 旧 DataSource 或自由文本 Analyzer 删除。

在上述能力具备独立离线测试、在线授权和迁移验收前，默认配置必须保持 `legacy`。
