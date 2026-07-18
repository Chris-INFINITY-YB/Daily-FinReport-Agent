# CN Profile Provider 字段契约与证据边界

更新时间：2026-07-18
状态：P1-03 离线骨架已完成；P1-04 受控观察失败已记录，无在线 Transport，尚无 CN Profile 脱敏 Fixture

本文只固定 CN Profile Provider 的身份、字段、结果和证据门禁。它不改变现有
[`CNDataSource`](../daily_report_agent/datasource/cn.py)，也不授权网络请求、生产接入或
持久化。通用迁移边界见 [`provider_migration.md`](provider_migration.md)，执行顺序见
[`parallel_development_plan.md`](parallel_development_plan.md)。

## 1. 证据等级

| 等级 | 含义 | 当前可否直接映射标准字段 |
|---|---|---|
| E0：标准契约 | 仓库内标准模型、Protocol 和调用方输入已经固定的语义 | 可以，但只能用于身份、时间和结果边界 |
| E1：旧链路本地证据 | 现有 `CNDataSource` 确实调用或读取的名称 | 不可以；只证明旧代码曾依赖该形状 |
| E2：上游字段假设 | 根据函数名、常识或未保存的印象提出的候选字段 | 不可以，必须返回 `None` 或拒绝映射 |
| E3：脱敏 Fixture 证据 | P1-04 用受控真实响应制作并经测试固定的脱敏样本 | 可以，且仍须满足身份和缺失值规则 |

当前没有 CN Profile Fixture，因此上游字段最高只有 E1。E1 不能升级为可信映射；P1-04
必须用脱敏 Fixture 固定响应结构、字段名、值类型和缺失形态后，字段才可进入 E3。
P1-03A 只确认原始来源归属，不提高任何响应字段的证据等级。

## 2. 旧 CNDataSource 的已知边界

仓库内现有代码提供以下本地证据：

- 通过延迟导入的 AkShare 客户端调用 `stock_individual_info_em(symbol=symbol)`；
- 假定返回表格包含 `item` 和 `value` 两列，再用 `dict(zip(...))` 转为键值对；
- 读取过 `股票简称`、`行业` 和 `总市值`；
- 仅在旧 `StockData.name` 为空时才以 `股票简称` 或请求代码补名称；
- 把名称、代码、行业和总市值拼成旧 `intro` 文本；
- 捕获所有异常且不保留结构化原因，无法区分空结果、结构变化和上游失败。

这些事实只说明旧链路的使用方式，不证明当前上游仍返回相同字段，也不证明字段类型、
空值标记、单位或稳定性。旧 `intro` 是展示字符串，不是 `SecurityProfile.description`。
尤其是总市值、流通市值、价格、估值和成交数据属于动态市场数据，不能进入稳定 Profile，
也不能拼进 `description`。

## 3. Provider 身份决策

### 3.1 原始来源与调用库分离

- 原始数据来源的目标身份：`eastmoney`；
- 调用库/客户端：AkShare；
- 当前调用入口：`stock_individual_info_em`；
- 禁止使用的 Provider ID：`akshare`、`akshare-profile`、`cn-profile`、
  `stock-individual-info-em` 或其他表示库、能力、市场或函数名的临时 ID。

选择 `eastmoney` 的理由是 Provider ID 应表示证据的原始发布来源，并在 Transport 或客户
端替换后保持稳定。`stock_individual_info_em` 的本地函数名为 Eastmoney 提供了明确候选，
而 AkShare 只是当前调用所用库。`eastmoney` 符合现有小写 ASCII、数字、短横线和下划线
规则，也不会把 Profile 能力编码进来源身份。

### 3.2 来源归属离线证据（P1-03A）

核验日期：2026-07-18

本次只静态读取本机已安装包和项目源码，没有导入 AkShare、调用
`stock_individual_info_em()` 或发送网络请求。为保证证据可移植，以下路径均相对于安装包
根目录，不记录本机绝对路径。

| 证据项 | 静态结果 | 证据作用 |
|---|---|---|
| AkShare 安装元数据 | 版本 `1.18.46` | 固定被审计的包版本 |
| Python 模块 | `akshare.stock.stock_info_em` | 表明目标函数位于专用 `stock_info_em` 模块 |
| 源码相对路径 | `akshare/stock/stock_info_em.py` | 提供可复核的包内位置 |
| 函数定义 | `stock_individual_info_em`，该版本第 13～69 行 | 与项目旧链路调用入口一致 |
| 函数说明 | `东方财富-个股-股票信息` | 明确命名原始发布方，而非仅依赖 `_em` 后缀 |
| 函数内上游主机名 | `push2.eastmoney.com` | 域名独立支持 Eastmoney 归属 |
| 静态请求调用符号 | `requests.get` | 证明 AkShare 在客户端边界发起调用；未执行该调用 |
| 包顶层导出 | `akshare.__init__` 从上述模块导出同名函数 | 连接项目所用公开入口与被审计实现 |
| 源码文件 SHA-256 | `3264436193901655cccf914560327ceb7fc7dbf919785984da6451ca5ea5d33f` | 固定本次审计的源码内容 |
| 项目本地调用 | `CNDataSource` 调用 `ak.stock_individual_info_em` | 连接安装包公开函数与当前旧链路 |

证据等级结论：函数说明和 `eastmoney.com` 上游主机名构成两项相互独立的强静态证据；
模块名、函数名和 `_em` 后缀只作为辅助证据，不单独承担归属判断。AkShare 是调用库，
不是原始数据来源。

### 3.3 门禁结论与限制

P1-03A 来源归属门禁已通过。P1-03 可以安全使用稳定 Provider ID `eastmoney`，不再因来源
身份阻塞，也不得改用表示客户端或能力的临时 ID。Descriptor 固定为：

```text
provider_id: eastmoney
display_name: Eastmoney
capabilities: {profile}
markets: {cn}
version: None
```

任何返回的 `SecurityProfile.source` 必须等于 Descriptor 的 `provider_id`，不得填写
`akshare` 或上游响应中的自由文本。

本结论只适用于上述版本和 SHA-256 对应的源码。升级 AkShare 或源码哈希变化时必须重新
核验模块说明和上游主机名。它不证明接口当前可用，也不证明真实响应结构、`item/value`
列、`股票简称`、`行业`、缺失值、证券身份或任何字段映射；这些仍须由 P1-04 脱敏 Fixture
和离线测试确认。P1-03A 结论本身只确认来源归属；离线骨架状态另见下一节。

### 3.4 P1-03B 离线骨架状态

完成日期：2026-07-18

P1-03B 已建立严格离线、显式注入的代码骨架：

- [`constants.py`](../daily_report_agent/providers/eastmoney/constants.py) 固定仅支持
  `PROFILE`/`cn` 的 `eastmoney` Descriptor，`version` 保持 `None`；
- [`transport.py`](../daily_report_agent/providers/eastmoney/transport.py) 只定义同步只读
  Protocol，输入为标准化代码，输出为 `tuple[Mapping[str, object], ...]`；没有默认实现；
- [`parser.py`](../daily_report_agent/providers/eastmoney/parser.py) 只消费传入行记录，
  精确识别候选 `股票简称` 和 `行业`，不接受 DataFrame，不联网；
- [`profile.py`](../daily_report_agent/providers/eastmoney/profile.py) 负责 CN 输入、六位 ASCII
  数字代码、aware clock、Transport 调用和安全错误映射；
- 导入和构造均不会调用 Transport，也不会加载 AkShare、pandas 或在线客户端；只有
  `fetch_profile()` 会调用显式注入的 Transport。

Parser 对内联 Fake 行的支持只是接口骨架测试，不把 `name` 或 `industry` 从 E1 升级为
E3。当前没有真实或脱敏响应 Fixture，也没有在线 Transport；不得据此声称 Eastmoney
Profile 已在线可用或真实字段映射已经验证。

### 3.5 2026-07-18 受控观察记录（P1-04R）

本记录只固化一次已经结束的受控观察失败，不构成 P1-04 完成证据，也不授权继续请求。

| 项目 | 安全记录 |
|---|---|
| 观察日期 | `2026-07-18` |
| 调用库/版本 | AkShare `1.18.46` |
| 函数与证券 | `stock_individual_info_em(symbol="600519")`；证券只用于静态公司资料结构验证 |
| 调用边界 | 只读单次调用；调用次数 `1`，自动重试 `0`；没有循环、并发或替代证券请求 |
| 结果 | 失败；安全错误类别为“响应不可用”，异常类型为 `JSONDecodeError` |
| 数据保留 | 未查看、输出或保存原始响应；未保存状态码、Content-Type、完整 URL、Header、Cookie 或 Token |
| Fixture | 未创建 observed Fixture，也未创建失败响应或伪造 Fixture |
| 审计源码 | `akshare/stock/stock_info_em.py`，SHA-256 为 `3264436193901655cccf914560327ceb7fc7dbf919785984da6451ca5ea5d33f` |

上述版本源码的目标函数先执行 HTTP 获取，再在本地调用 `r.json()` 反序列化响应；只有该
步骤成功后，才会构造 DataFrame、映射字段标签并最终生成 `item/value` 两列。静态源码中
唯一明确的 JSON 解析边界是 `r.json()`。因此本次 `JSONDecodeError` 发生在可供 Eastmoney
Transport 或项目 Parser 消费的 `item/value` 行记录形成之前；项目 Parser 没有收到真实
行记录，本次也没有观察到任何真实字段名、字段值、值类型、空值或证券身份字段。

由于没有保留 traceback、HTTP 状态码、Content-Type 或响应正文，本记录不能确认失败的
具体上游原因。HTTP 限流、WAF/反爬拦截、HTML 错误页、接口永久失效、字段协议变化和
AkShare 缺陷都只能列为未验证候选原因，不能据此归因。`JSONDecodeError` 也不证明上述
任一候选原因成立。

本次失败不改变第 1 节证据等级：`name` 和 `industry` 仍为 E1，不能升级到 E3；
`item/value` 结构、真实字段类型与缺失形态仍未验证。P1-04 保持未完成。任何后续尝试都
必须作为新的受控观察单独审批、单独计数和单独记录，不能作为本次调用的重试或继续执行。

## 4. 字段证据表

标准模型以 [`SecurityProfile`](../daily_report_agent/models/profile.py) 为准，调用身份以
[`Security`](../daily_report_agent/models/security.py) 为准。

| 标准字段 | 候选上游字段或来源 | 转换与规范化规则 | 缺失值规则 | 当前证据等级 | 升级为可信字段所需条件 |
|---|---|---|---|---|---|
| `symbol` | 调用方 `Security.symbol` | 使用已标准化并去除边界空白的请求值；响应代码只能交叉校验，不能覆盖 | 请求值无效则抛 `ProviderValidationError`，不得返回 item | E0，已确认身份语义 | P1-04 增加响应身份一致、缺失和冲突测试 |
| `market` | 调用方 `Security.market` | 仅接受 `cn`；固定返回请求值；上游内容不能改变市场 | 非 `cn` 抛 `ProviderValidationError` | E0，已确认身份语义 | P1-04 增加非 CN 输入和身份冲突测试 |
| `name` | `item/value` 中候选 `股票简称` | E3 后仅接受去除边界空白的非空字符串；不以代码、调用方名称或其他字段补齐 | 未验证、缺列、空值或类型不符均为 `None` | E1，旧代码曾读取；当前不可信 | 脱敏 Fixture 证明列结构、字段名、值类型、空值形态和证券归属 |
| `exchange` | 当前无本地候选字段 | 不从代码前缀、市场常识或调用方已有值推测；没有独立来源字段就不映射 | `None` | 无字段证据，暂不支持 | Fixture 提供含义明确的交易所字段，并固定允许值及规范化规则 |
| `currency` | 当前无本地候选字段 | 不因市场为中国或价格通常以人民币计价而自动填 `CNY` | `None` | 无字段证据，暂不支持 | Fixture 和独立字段说明共同证明币种语义及值域 |
| `industry` | `item/value` 中候选 `行业` | E3 后仅接受去除边界空白的非空字符串；不拼接概念、板块或其他分类 | 未验证、缺列、空值或类型不符均为 `None` | E1，旧代码曾读取；当前不可信 | 脱敏 Fixture 证明字段名、分类口径、值类型和缺失形态 |
| `description` | 当前无独立文本字段；旧 `intro` 不可复用 | 不拼接名称、代码、行业、市值或行情；没有独立公司描述证据就不映射 | `None` | 无字段证据，暂不支持 | Fixture 提供独立描述字段，并证明它不是动态行情、营销文本或字段拼接 |
| `source` | 固定 Provider Descriptor | 固定为 `eastmoney`；不读取响应自由文本，不使用库名 | 不能缺失 | E0 规则及 P1-03A 来源归属已确认 | P1-03 固定 Descriptor 测试；AkShare 版本或源码哈希变化时重新核验归属 |
| `fetched_at` | 注入的 clock | 使用本次成功获取/解析时由调用方注入的 timezone-aware `datetime`；不得调用隐式本地时间 | clock 返回 naive 或非 datetime 时抛 `ProviderValidationError` | E0，模型已确认 | P1-04 增加固定时钟、naive 时间和非 datetime 测试 |

### 4.1 通用缺失值规则

- 未达到 E3 的上游字段一律返回 `None`，不得根据字段外观、市场常识、证券代码或其他
  字段推测；
- 不得用 `str(None)`、`str(NaN)`、`"未知"`、`"None"` 或拼接文本制造有效值；
- 上游缺列、空字符串、空白字符串、null、NaN 和其他哨兵值的精确识别范围由 P1-04
  Fixture 固定；未经 Fixture 观察的哨兵不擅自扩展；
- `symbol`、`market`、`source` 和 `fetched_at` 是构造 item 的必需字段，不能以 `None`
  降级；
- 调用方身份字段不计作上游资料。若所有达到 E3 的资料字段都缺失，应返回空 `items`，
  不构造只有身份和时间的空壳 Profile；
- 上游若返回证券代码或市场，只能用于一致性校验。冲突时整个请求失败，绝不能悄悄改变
  调用方请求的证券身份。

## 5. 当前支持边界

P1-03B 的纯 Parser 已按精确候选名称处理内联 Fake 行，但这不构成上游字段证据。在
P1-04 Fixture 验证前，只有身份、来源和时间规则得到固定；没有任何上游资料字段获准
进入真实 Transport 映射。Fixture 验证后，首批拟支持字段仅为 `name` 和 `industry`。
`exchange`、`currency` 和 `description` 继续保持 `None`，直到各自取得独立证据。

“完整资料”按当前获准支持的字段集合判断，而不是要求所有标准字段非空。例如首批只在
`name` 和 `industry` 均有可信值时视为完整；尚未支持的 `exchange`、`currency` 和
`description` 为 `None` 不产生缺失 Issue。任何新增支持字段都必须先更新本文和 Fixture。

## 6. 结果与错误语义

`fetch_profile()` 的操作标识固定为 `fetch_profile`。成功请求返回
[`ProviderResult`](../daily_report_agent/providers/contracts.py)；请求级失败抛出
[`ProviderError`](../daily_report_agent/providers/errors.py) 子类。两者不能互相冒充。

| 场景 | `ProviderResult.items` | `DataIssue` | `ProviderError` |
|---|---|---|---|
| 成功且有完整资料 | 一个 `SecurityProfile` | 空 | 不抛出 |
| 成功但部分可选字段缺失 | 一个 `SecurityProfile`，缺失字段为 `None` | `WARNING/MISSING_DATA`，建议 code=`missing_profile_fields`，只列安全字段名 | 不抛出 |
| 成功但无资料 | 空元组 | `INFO/MISSING_DATA`，建议 code=`profile_not_found` | 不抛出；空结果明确表示请求成功 |
| 响应结构无法识别 | 不返回结果 | 无结果可附加 Issue | 抛 `ProviderParseError`，建议 code=`invalid_profile_response` |
| 响应身份与请求冲突 | 不返回结果 | 无结果可附加 Issue | 抛 `ProviderValidationError`，建议 code=`identity_mismatch` |
| 市场或证券输入不支持 | 不返回结果 | 无结果可附加 Issue | 抛 `ProviderValidationError`，建议 code=`unsupported_market` 或 `invalid_security` |
| 上游网络或超时 | 不返回结果 | 无结果可附加 Issue | 抛 `ProviderNetworkError` 或 `ProviderTimeoutError` |
| 上游限流 | 不返回结果 | 无结果可附加 Issue | 抛 `ProviderRateLimitError` |
| 上游阻断或不可用 | 不返回结果 | 无结果可附加 Issue | 抛 `ProviderBlockedError` 或 `ProviderUnavailableError` |

`DataIssue` 只描述成功响应中的数据质量或空结果，不承载请求级失败。所有
`ProviderError.safe_message`、code 和 Issue 内容必须安全外显，不包含 Token、Cookie、
完整 URL、原始响应、底层 traceback 或真实敏感字段值；底层异常只通过异常链保留。

## 7. P1-04 必须补充的证据

P1-04 至少需要以下脱敏 Fixture 和测试，才能把 E1 字段升级为 E3：

1. 正常 `item/value` 表格，证明 `股票简称` 和 `行业` 的实际字段名、类型及证券归属；
2. `name` 或 `industry` 单独缺失、为空以及两者同时缺失；
3. 空响应和结构可识别但无资料的响应；
4. 缺少 `item`/`value` 列、重复 item、未知 item 和非字符串值；
5. 响应证券身份一致、身份缺失和身份冲突；
6. 上游实际空值/哨兵形式，避免把 `NaN`、`None` 或展示占位符变成字符串；
7. Fixture 元数据引用第 3.2 节归属证据，继续区分 Eastmoney 原始来源与 AkShare 客户端；
8. 固定 aware clock、naive clock 和非法 clock；
9. 网络、超时、限流、不可用和安全错误文本映射。

Fixture 必须手工脱敏，不保存 Cookie、Token、完整请求 URL 或未脱敏原始响应。P1-02
不运行真实网络请求，也不以猜测填补上述证据。

## 8. 当前仍明确不做

- 不实现真实或在线 Transport，不导入 AkShare、pandas、requests 或其他在线客户端；
- 不增加脱敏 Fixture，不声称真实响应结构或字段映射已经验证；
- 不新增数据库 migration，不持久化 `SecurityProfile`；
- 不修改旧 `CNDataSource` 或正式 DataSource 路由；
- 不接入 Analyzer、Prompt、Report 或通知；
- 不读取正式配置，不运行真实网络请求；
- 不访问、迁移、覆盖或删除 B4 观察库；
- 不改变腾讯 Shadow 的配置、代码或验收状态。
