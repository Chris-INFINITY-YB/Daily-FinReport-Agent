# 腾讯 QuoteProvider B4 五日观测验收记录

五日证据验收日期：2026-07-24
合并候选门禁补充日期：2026-07-25
验收结论：B4 五日观测及合并候选门禁均通过；腾讯 Provider 尚未获准进入正式路由

## 1. 验收范围与日期

本记录对 2026-07-20 至 2026-07-24 五个连续交易日的腾讯 QuoteProvider Shadow
观测证据进行只读验收。证据来自项目目录外的 B4 持久 SQLite 数据库及五个逐日审计目录。
验收期间没有重新执行真实观测、发送网络请求、运行 migration 或修改观察数据。

本结论只覆盖固定 3 个证券、每日一次、单个串行批次、无并发、无自动重试的受控条件。
它不是生产 SLA 证明，也不能外推到全部 A 股证券。

## 2. 冻结提交和执行环境

| 项目 | 验收值 |
|---|---|
| 冻结观测提交 | `98229c67e1a33f83260684b081c7097d92c76c03` |
| 冻结观测入口 SHA-256 | `f645ce9dfa4c72faae90f132f0f373d31f2428df1caff31229ed3fc4d4b74bc0` |
| 每日 Python | `/opt/anaconda3/envs/quant_env/bin/python`，Python 3.10.20 |
| B4 数据库 | 项目目录外独立 SQLite |
| 观察模式 | 每个交易日一次，固定证券，串行，无自动重试 |

五份 preflight 均记录相同冻结提交和入口哈希。当前开发分支相对冻结提交没有修改腾讯
观测入口、腾讯 Provider、Shadow Pipeline、storage 实现或默认配置。当前入口文件复算
SHA-256 与冻结值一致。

## 3. 固定证券及观察粒度

固定证券为 `600519`、`300750`、`000001`。每天请求 3 个证券，执行一次逻辑
`fetch_quotes()` 和一次底层请求；每次调用均为单个串行批次，`retry_count=0`，不并发。
数据库只保留标准 `MarketSnapshot` 和调用元数据，不保存原始响应。

## 4. 五日逐日结果

| 交易日 | ProviderCall | `item_count` | `retry_count` | `duration_ms` | Snapshot 新增 | 缺失证券 | Data issue | RawResponse 新增 | manifest |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| 2026-07-20 | success | 3 | 0 | 1447 | 3 | none | 0 | 0 | 通过 |
| 2026-07-21 | success | 3 | 0 | 4083 | 3 | none | 0 | 0 | 通过 |
| 2026-07-22 | success | 3 | 0 | 4540 | 3 | none | 0 | 0 | 通过 |
| 2026-07-23 | success | 3 | 0 | 2003 | 3 | none | 0 | 0 | 通过 |
| 2026-07-24 | success | 3 | 0 | 1546 | 3 | none | 0 | 0 | 通过 |

五天输出还一致记录 `issue_codes: none`、`raw_responses_added: 0`。五次 observation 和
summary 退出码均为 0，observation/summary stderr 均为空。

## 5. 数据质量和隔离检查

### 完整性

- `PipelineRun=5`、`ProviderCall=5`、`MarketSnapshot=15`、`RawResponse=0`；
- 5 次 ProviderCall 均为 `success`，每次 `item_count=3`；
- 三只固定证券各有 5 条快照；
- 15 条快照的 `price`、`previous_close`、`pct_change` 均无空值；
- 15 条快照的货币均为 CNY。

验收只核对字段状态和计数，本文不记录真实价格。

### 唯一性

- `security_id + source + observed_at` 重复业务键组为 0；
- 请求指纹非空且只有 1 种，证明五天使用同一组固定标的；本文不记录指纹值。

### 有效性

- 5 次调用的 `retry_count` 均为 0，Provider 错误元数据行数为 0；
- 五天均无缺失证券、Data issue 或 Issue code；
- 审计证据中没有 403、429、timeout、网络错误或 Parser issue 暂停信号；
- 15 条快照均能关联到证券，孤立快照为 0。

### 新鲜度

每只固定证券在每个交易日都有一条当日快照，观察日期连续覆盖 2026-07-20 至
2026-07-24；没有证券缺失或陈旧行情暂停信号。该检查只证明本次每日单点样本的新鲜度，
不证明盘中连续更新能力。

### 正式链路隔离

- 五天正式数据库在观测前后均不存在；
- 观测入口没有触发正式分析、报告、通知或行情路由；
- `raw_responses` 始终为 0；
- `storage.enabled` 和 `providers.tencent_quote.shadow_enabled` 仍为 `false`；
- 腾讯 QuoteProvider 仍是默认关闭的 Shadow Provider。

## 6. 延迟统计

五日耗时依次为 1447、4083、4540、2003、1546 ms，算术平均值为 2723.80 ms，最大值
为 4540 ms。这是五个低频串行样本的描述性统计，不构成生产延迟 SLA。

## 7. 审计清单验证

逐一在对应目录运行 SHA-256 清单校验：

| 审计目录 | 清单结果 |
|---|---|
| `runs/20260720` | 全部条目通过 |
| `runs/20260721` | 全部条目通过 |
| `runs/20260722` | 全部条目通过 |
| `runs/20260723` | 全部条目通过 |
| `runs/20260724` | 全部条目通过 |

五个 `audit_manifest.sha256` 均以退出码 0 完成验证。验收前后复算的 B4 数据库 SHA-256
以及五个审计目录聚合摘要逐一相同，确认本次只读验收没有改变证据资产。

## 8. 已知测试时间依赖问题

冻结提交的观察 Fixture 使用固定时间 `2026-07-14T07:00:00Z`，而汇总 CLI 原先直接使用
真实当前 UTC 时间。自 2026-07-21 起，固定样本逐渐落在测试传入的 7 日窗口之外，导致
完整冻结测试出现 1 个时间敏感断言失败；同日针对观测运行逻辑的其余断言和定向测试通过，
该问题不影响真实 B4 观测数据。

当前候选改动已为汇总 `main()` 增加默认保持当前 UTC 的可注入 clock，并让测试显式使用
固定的 timezone-aware UTC 时间。窗口仍为 7 天，CLI 参数和默认运行行为不变；naive 或
非 datetime clock 会被安全拒绝。

## 9. 验收结论

2026-07-20 至 2026-07-24 的 B4 连续五个交易日观测已经完成，五日观测证据通过当前 B4
数据质量验收。

这个结论不表示腾讯 Provider 已达到生产 SLA、覆盖全部 A 股、成为正式或备用行情源，
也不表示路由、降级、缓存、限流、重试或熔断已经通过验收。腾讯 Provider 尚未获准进入
正式路由，仍保持默认关闭的 Shadow 身份。

## 10. 合并候选门禁最终状态

合并候选固定为：

```text
b921a8e8a541551e19af069666d9be3edba2fa3d
```

### 10.1 完整离线回归

- Python 3.10.20：`403 passed`；
- Python 3.13.9：`403 passed`；
- `compileall`、普通 dry-run、固定日期 dry-run、`git diff --check` 均通过；
- Prompt SHA-256 保持
  `7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3`；
- 固定日期 dry-run SHA-256 保持
  `8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0`；
- 本地无索引构建、安装和安装后导入通过；
- 默认 `storage.enabled=false`、`providers.tencent_quote.shadow_enabled=false`；
- 未创建正式数据库，未发送网络请求。

### 10.2 同候选受控在线验证

2026-07-25 在单独授权后，对上述精确候选执行一次受控在线验证：

| 项目 | 结果 |
|---|---|
| 执行日期 | 2026-07-25，Asia/Shanghai |
| smoke 入口 SHA-256 | `b2010b61ffcf947e566e5634c8abed24c1e46faafa70573306600d4a6e219b8f` |
| Shadow 观测入口 SHA-256 | `f645ce9dfa4c72faae90f132f0f373d31f2428df1caff31229ed3fc4d4b74bc0` |
| 逻辑 Provider 调用 | 1 |
| 串行批次 / 底层请求 | 1 / 1 |
| 并发 / `retry_count` | 0 / 0 |
| Provider 状态 | `success` |
| 请求 / 返回 / `item_count` | 3 / 3 / 3 |
| 缺失证券 | none |
| Issue / issue code | 0 / none |
| 403 / 429 | none |
| timeout / network | none |
| Parser | 三条记录解析及字段交叉校验均通过 |
| 墙钟耗时 | 4.76 秒 |

候选副本执行前后逐文件一致，没有新增或修改文件；没有创建正式数据库、pytest cache 或
RawResponse 资产，没有保存响应正文、真实价格、完整 URL、请求指纹、Header、Cookie 或
Token。当前工作区、B4 数据库及五日审计资产摘要验收前后相同，且没有第二次网络请求。

### 10.3 门禁结论

以下候选门禁均已完成：

1. 合并候选完整离线回归；
2. 同一候选的单次受控在线验证；
3. 默认配置、正式数据库、正式 Pipeline、报告和通知隔离复核。

这些结果只允许另立任务评审正式路由、降级、缓存、限流、重试和熔断，不表示相关能力已
设计、验收或启用，也不把腾讯提升为正式或备用行情源。

## 11. 远端门禁与后续动作

P4-01R 验收提交 `b7fa7386b211579aaa1999f415acc9da07436119` 已在 2026-07-26 的
GitHub Actions 运行
[`30193535946`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30193535946)
中通过 Python 3.10/3.13 两个远端 Job，均为 `407 passed`。这只把 P4-01 从本地完成
升级为远端门禁通过，不改变本文件的 B4 历史观察结论，也不表示腾讯已晋级为正式或备用
行情源。P1-04 Eastmoney CN Profile observed Fixture 在 2026-07-25 的第二次独立受控
观察中仍未形成；任何未来在线观察、Provider 功能或腾讯正式接入设计仍须重新单独授权。
