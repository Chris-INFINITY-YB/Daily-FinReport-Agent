# 腾讯 QuoteProvider 离线 Fixture

这些文件是为解析契约手工整理的固定、最小化腾讯文本格式样本。它们不包含 Cookie、
Header、token、用户标识或请求日志，不会在测试时在线刷新，也不构成腾讯接口 SLA 或
版本保证。证券代码是公开 A 股代码；名称和数值仅用于离线测试。

## 文件与场景

| 文件 | 场景 | 主要覆盖 |
|---|---|---|
| `quote_single_sh.txt` | 上海单证券 | 代码、现价、昨收、涨跌幅、北京时间 |
| `quote_single_sz.txt` | 深圳单证券 | 深圳前缀和相同标准字段 |
| `quote_batch_mixed.txt` | 沪深混合且响应逆序 | 根据响应代码匹配并按请求顺序输出 |
| `quote_zero_change.txt` | 真实零涨跌 | `pct_change=0.0` 不变成缺失值 |
| `quote_status_51.txt` | 在线观察到的完整行情状态变体 | 首字段 `51`、88 字段、核心行情位置不变 |
| `quote_suspended_or_partial.txt` | 部分字段缺失、无可靠时间 | 缺失数值为 `None`，时间回退并产生 issue |
| `quote_empty.txt` | 明确的空数据记录 | 成功无数据，不伪造快照 |
| `quote_malformed.txt` | 一条合法、一条损坏 | 部分成功并产生 PARSE warning |
| `quote_all_malformed.txt` | 全部损坏 | 抛出安全 `ProviderParseError` |

## 字段契约

Fixture 保留腾讯文本记录的波浪线分隔位置。本阶段只映射经过契约固定的字段：

| 位置 | 含义 | 标准字段 | 单位/解释 |
|---:|---|---|---|
| 0 | 记录状态 | Parser 协议判断 | 在线已观察到 `1` 和 `51` 两种完整行情值 |
| 2 | 六位证券代码 | `symbol` | 无单位，移除 `sh/sz` 响应前缀 |
| 3 | 当前价格 | `price` | CNY/股 |
| 4 | 昨收 | `previous_close` | CNY/股 |
| 30 | 行情时间 | `observed_at` | `YYYYMMDDHHMMSS`，显式使用 Asia/Shanghai |
| 32 | 涨跌幅 | `pct_change` | 百分比数值，`2.50` 表示 2.50% |

响应中用于展示协议形状的成交量、成交额、换手率、PE 和 PB 位置没有映射：成交量的
“手/股”、成交额的缩放单位以及 PE 的动态/TTM 口径缺少稳定官方契约，市值字段也存在
缩放口径问题。它们统一保持 `None`，不能从 Fixture 数字猜测标准单位。`currency` 根据
已验证的 CN A 股支持范围固定为 `CNY`，`source` 固定为 `tencent-finance`。

空字符串和 `--` 表示缺失，不能转换成零；文本 `0.00` 是真实零值。

`quote_status_51.txt` 是首次受控在线冒烟发现差异后手工构造的脱敏最小样本，不是在线
响应的自动拷贝；它保留实测 88 字段形状，但没有保存 Header、URL 或原始正文。
