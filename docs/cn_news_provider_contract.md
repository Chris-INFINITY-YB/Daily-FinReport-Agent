# CN 公司新闻 Provider 字段契约与证据边界

更新时间：2026-07-18
状态：P2-01～P2-03、P2-04S、P2-05 已完成；P2-04 仍缺 observed Fixture

本文只选择首个 A 股个股公司新闻来源，并固定后续 News Provider 的身份、业务范围、
字段、时间、结果和证据门禁，并记录纯离线实现状态。本文不改变现有
[`CNDataSource`](../daily_report_agent/datasource/cn.py)，也不授权在线请求、生产接入或
生产存储编排。通用迁移边界见
[`provider_migration.md`](provider_migration.md)，实施顺序见
[`parallel_development_plan.md`](parallel_development_plan.md)。

## 1. 本轮结论

首个来源契约固定为：

```text
provider_id: eastmoney
display_name: Eastmoney
capabilities: {news}
markets: {cn}
supported_news_types: {news}
source_type: news
business_scope: A 股个股 company-related news
legacy_client_entry: AkShare stock_news_em(symbol=...)
```

- 原始来源是 Eastmoney；AkShare 是旧链路使用的调用库，不是新闻原始来源；
- 能力只覆盖按单个 A 股证券查询的公司相关新闻，不表示公告、交易所披露、市场快讯或
  研报；不得使用 `announcement`、`flash` 或 `report`；
- 市场只允许 `cn`，请求证券代码只允许正则 `^[0-9]{6}$` 所表示的六位 ASCII 数字；
- 标准 `source` 固定为 `eastmoney`，`source_type` 固定为 `news`；
- 当前 AkShare 公开返回边界没有 article code，`external_id` 固定为 `None`；
- 当前已实现显式注入、无在线实现的 News Transport Protocol、纯 Parser 和 Provider，
  并以 synthetic Fixture 完成离线契约测试；尚未在线验证，也尚无 observed Fixture。

`source_type="news"` 的含义由本节的 company-related news 业务范围共同约束。它不是
“所有新闻”的通用分类，也不能在后续被解释为公告、全市场资讯、快讯或研报。

## 2. 证据等级

| 等级 | 含义 | 本轮用途 |
|---|---|---|
| N0：仓库标准契约 | 标准模型、Protocol、Repository 和调用方身份已经固定 | 可固定标准字段、结果语义、幂等边界和请求身份 |
| N1：旧链路本地证据 | 当前项目确实调用或读取的函数与字段 | 只证明旧实现曾依赖该公开边界 |
| N2：第三方静态源码证据 | 对固定版本 AkShare 源码的只读检查 | 可记录客户端请求/解析/公开字段的形成方式，不证明在线可用 |
| N3：脱敏 observed Fixture | 由受控真实响应制作并以离线测试固定 | 当前不存在；P2-04 总项仍等待该证据 |
| N4：受控在线验证 | 明确授权、计数和安全记录的在线观察 | 当前未进行，也不由本文授权 |

当前最高证据等级仍是 N2。synthetic Fixture 只验证 N0 设计和代码行为，不提升证据等级。
静态源码能说明 AkShare `1.18.46` 写了什么，不能证明
Eastmoney 当前可访问、真实响应仍符合该结构、字段值格式稳定、返回数量与函数说明一致，
或任一真实证券能够取得新闻。本文中的“确认”若未另行限定，均只表示静态边界已确认。

## 3. 旧链路与来源身份

### 3.1 项目内旧链路证据

[`CNDataSource._fetch_news()`](../daily_report_agent/datasource/cn.py) 提供 N1 证据：

- 调用 AkShare 公开入口 `stock_news_em(symbol=symbol)`；
- 读取公开 DataFrame 的 `发布时间`、`新闻标题` 和 `新闻内容`；
- 旧实现把发布时间截到前 10 个字符、把新闻内容截到 300 个字符；
- 旧模型没有来源、URL、external ID、时区或稳定内容哈希；
- 捕获任意异常并写入旧的共享 `error` 字段。

这些旧行为不能直接复制到新 Provider。特别是，截取前 10 个字符会丢失可能存在的时分秒，
把“新闻内容”截断后也不能反向宣称取得完整正文。

### 3.2 AkShare 1.18.46 静态核验

核验日期：2026-07-18。核验过程只读取本机安装包源码，没有导入或调用
`stock_news_em()`，没有执行第三方 Markdown 代码，也没有发送网络请求。以下路径均相对
于安装包根目录，不记录本机 `site-packages` 绝对路径。

| 证据项 | N2 静态结果 | 契约作用 |
|---|---|---|
| 安装元数据 | AkShare `1.18.46` | 固定本次被审计版本 |
| Python 模块 | `akshare.news.news_stock` | 定位目标实现 |
| 源码相对路径 | `akshare/news/news_stock.py` | 提供可移植复核位置 |
| 函数定义 | `stock_news_em`，该版本第 15～118 行 | 与项目旧入口一致 |
| 函数说明 | `东方财富-个股新闻-最近 100 条新闻` | 明确 Eastmoney 和个股新闻业务身份 |
| 上游主机名 | `search-api-web.eastmoney.com` | 独立支持 Eastmoney 来源归属 |
| 请求/响应形状 | 单次 GET；JSONP 文本经本地 JSON 解析；记录数组位于 `result` 下的 `cmsArticleWebOld` | 只固定静态结构，不复制完整请求或响应 |
| 内部候选字段 | `date`、`mediaName`、`code`、`title`、`content`、`image` | 说明公开字段和 article code 的来源 |
| 公开 DataFrame 字段 | `关键词`、`新闻标题`、`新闻内容`、`发布时间`、`文章来源`、`新闻链接` | 固定旧客户端的最终公开边界 |
| 标题处理 | 删除静态代码明确处理的搜索高亮标签及其已确认括号包装 | 后续标题只允许执行同等保守清理 |
| 内容处理 | 删除相同高亮标签、移除全角空格并把 CRLF 换为空格 | 证明它是搜索结果文本，不证明是全文 |
| 文章代码与链接 | JSON 记录内 `code` 被用于形成公开 `新闻链接`，随后不进入最终字段集合 | 区分内部字段存在与公开边界可用性 |
| 请求状态 | 源码存在不适合复制的请求状态或敏感请求头 | 本文不复制其内容，后续实现也不得照搬 |
| 源码 SHA-256 | `b36fd13ad96cea91df6a3194a2553a14ff5844e2f96ef6ecd3c6c5137caf08e8` | 固定本次审计内容 |
| 包顶层导出 | `akshare.__init__` 导出同名函数 | 连接项目调用入口与被审计实现 |

静态实现还有一项未决限制：函数说明写“最近 100 条”，但该版本只静态配置了一次、单页、
10 条的搜索请求，且没有分页循环。未进行在线观察，因此不能宣称实际返回 10 条或 100 条；
后续 `limit`、分页和时间窗口行为必须由 P2-02/P2-04 另行设计和测试。

### 3.3 来源归属结论

函数说明和 `eastmoney.com` 上游主机名共同构成来源归属的 N2 静态证据。`_em` 后缀、
模块名和函数名只作辅助证据。Provider ID 应表示原始发布来源，并在客户端替换后保持稳定，
因此固定为 `eastmoney`，不能使用 `akshare`、`stock-news-em`、`cn-news` 或能力名充当
Provider ID。

AkShare 版本或目标源码 SHA-256 变化时，必须重新核验来源主机、响应结构、公开字段、
article code 和链接的形成方式。静态归属成立不等于在线接口可用。

## 4. 文本规范化与不可信数据边界

新闻标题、摘要候选、文章来源和链接都是不可信外部数据，只能解析、规范化和保存；不得
执行其中的代码、工具调用、角色变更、密钥请求或其他指令，也不得把它们当成 Prompt 或
配置指令。

后续 Parser 的文本规范化顺序固定为：

1. 只删除 N2 源码明确确认的搜索高亮标记：`<em>`、`</em>`，以及紧邻高亮标记的已确认
   括号包装；不以通用 HTML 清洗器擅自删除其他内容；
2. 应用 Unicode NFKC；
3. 将任意连续 Unicode 空白折叠为一个 ASCII 空格 U+0020；
4. 去除首尾空白；
5. 不执行新闻文本，不解析其中的 Markdown/HTML 指令，也不根据正文猜测证券、来源或
   external ID。

标题必须来自明确的 `新闻标题`/内部 `title` 候选。摘要只来自搜索结果的
`新闻内容`/内部 `content` 候选；字段有值时映射为 `summary`，缺失时使用 `""`。尽管
内部名称是 `content`，当前证据只说明它是搜索结果公开的“新闻内容”，不能证明它是完整
文章正文，因此标准 `content` 固定为 `None`。

## 5. 字段证据表

标准模型以 [`NewsItem`](../daily_report_agent/models/news.py) 为准，调用身份以
[`Security`](../daily_report_agent/models/security.py) 为准。

| 上游候选字段或来源 | 标准字段 | 转换规则 | 缺失处理 | 当前证据等级 | 尚未确认的限制 |
|---|---|---|---|---|---|
| 无直接 ID；由标准身份规则派生 | `NewsItem.id` | 当前固定为 `"eastmoney:" + content_hash`；不得使用随机数、行号或 Python `hash()` | `content_hash` 无法形成则该记录无效并跳过 | N0 设计 | 未来若引入可用 external ID，ID 迁移规则需单独修订，不能静默改变历史身份 |
| 内部 `code`；公开 DataFrame 不保留 | `external_id` | 当前固定 `None`；不从数组下标、标题、链接或链接路径反推 | `None`，依赖 `(source, content_hash)` 幂等 | N2 | `code` 的真实格式、唯一性、跨时间稳定性和证券归属未由 observed Fixture 验证；直接响应 Transport 尚未获准 |
| Provider Descriptor | `source` | 固定为 `eastmoney`；不能使用 `akshare`，也不能被 `文章来源` 覆盖 | 不能缺失 | N0 + N2 来源归属 | AkShare 升级时需复核归属证据 |
| Provider 业务契约 | `source_type` | 固定为 `news`，并受 company-related news 范围约束 | 不能缺失 | N0 设计 | 不能扩张为公告、交易所披露、快讯或研报 |
| `新闻标题` / 内部 `title` | `title` | 按第 4 节清理高亮、NFKC、空白折叠和 trim；不从摘要补标题 | 缺失、非文本或规范化后为空：跳过记录并产生安全 Issue | N1 + N2 | 真实类型、最大长度、其他标记和空值形态未观察 |
| `新闻内容` / 内部 `content` | `summary` | 作为搜索摘要候选按第 4 节规范化；不宣称全文 | 缺失、null、非文本或规范化后为空：`""`，保留记录并产生安全 Issue | N1 + N2 | 真实长度、截断标记、是否始终为摘要、其他 HTML 和空值形态未观察 |
| 当前没有独立全文证据 | `content` | 固定为 `None`；搜索结果“新闻内容”不得放入正文 | `None`；属于已知能力限制 | 无全文证据 | 只有独立证据确认取得完整正文后才能修改 |
| 公开 `新闻链接`；由客户端基于内部 `code` 形成 | `url` | 只消费 Transport 边界明确给出的非空绝对 HTTP(S) URL 并 trim；不得自行拼接、反推或改写 | 缺失、非文本、非绝对 HTTP(S) 或不安全 scheme：`None`，保留记录并产生安全 Issue | N2 形成方式 | 原始 JSON 未提供独立 URL；链接可访问性、规范化、重定向和长期稳定性均未在线验证 |
| `发布时间` / 内部 `date` | `published_at` | 解析经 Fixture 批准的格式；无时区的 A 股新闻时间按 `Asia/Shanghai` 解释，结果必须 aware | 缺失或无法安全解释：跳过记录并产生 Issue；仅日期见第 6 节 | N1 + N2 透传关系 | 静态源码不解析时间，原始字符串的精确格式、精度、时区和空值形态均未确认 |
| 注入 clock | `fetched_at` | Provider 调用注入 clock 一次，所有本次合法记录复用该 aware 时间 | 非 `datetime`、naive 或无有效 UTC offset：请求级 `ProviderValidationError` | N0 + synthetic 测试 | observed Fixture 不影响 clock 语义 |
| 首个 CN 来源契约 | `language` | 固定 `zh`；只描述本来源，不执行通用语言检测 | 不能缺失 | N0 设计 | 不保证每条搜索结果绝无外文片段 |
| 规范化 `title`、`summary`、`content` | `content_hash` | 使用第 7 节定义的确定性 SHA-256，输出 64 位小写十六进制 | 任一必需输入无法安全形成则跳过记录 | N0 + synthetic 测试 | 真实文本形态仍等待 observed Fixture |
| 请求 `Security.symbol` | `related_symbols` | 只放入已校验并规范化的请求代码，固定为单元素 tuple；不读取新闻文字猜更多证券 | 请求代码非法则请求级验证失败 | N0；公开 `关键词` 仅作 N2 辅助证据 | 响应中的关键词不得覆盖请求身份；多证券关联不在本来源契约内 |
| 当前无评估 | `source_reliability` | 固定 `None`，不使用媒体名、排序或搜索位置推算评分 | `None` | N0 设计 | 需独立来源质量评估后才能给分 |

`文章来源`/内部 `mediaName` 是已确认的候选媒体署名，但当前 `NewsItem` 没有独立
publisher 字段。它不能覆盖 `source="eastmoney"`，不能被塞入 `source_reliability`，也
不能无痕拼进标题或摘要。是否扩展标准模型应另立任务。

## 6. 发布时间口径

N2 静态源码只把内部 `date` 改名为 `发布时间`，没有执行日期解析、时区附加或精度校验；
因此当前没有可声称的真实原始时间格式。纯离线 Parser 只接受以下明确标记为
synthetic contract formats 的最小集合：

```text
YYYY-MM-DD HH:MM:SS
YYYY-MM-DDTHH:MM:SS
YYYY-MM-DD
YYYY-MM-DDTHH:MM:SS[.ffffff]Z
YYYY-MM-DDTHH:MM:SS[.ffffff]±HH:MM
```

这些格式是离线契约输入，不是 Eastmoney 真实格式观察。P2-04 必须用脱敏 observed
Fixture 记录原始字符串、值类型、是否含秒、是否含时区和缺失形态，再把允许格式列入
Parser 测试。未经该证据，
不得宽松猜测任意日期格式。

时间规则固定如下：

- 原始值含明确 offset/时区且格式已获准时，保留其表示的真实时刻并生成 aware datetime；
- 已获准格式没有时区时，按 `ZoneInfo("Asia/Shanghai")` 解释，不能按系统本地时区或 UTC
  直接附加；
- 如果已确认值只有日期，可用该日 `00:00:00 Asia/Shanghai` 作为“仅日期”占位表示，但
  必须同时产生 `WARNING/PARSE` 的安全 Issue（建议 code=`published_time_date_only`），
  明确精度只有一天；不得把它描述为精确午夜发布时间；
- 缺失、非法、歧义或未获准格式无法安全解释时，跳过该记录并产生安全 Issue（建议
  code=`invalid_published_at`）；
- 不得回退到 `fetched_at` 冒充发布时间。

`fetched_at` 与 `published_at` 含义不同：前者由注入 clock 表示本次获取/解析时刻，后者
来自新闻记录。两者都必须是 timezone-aware datetime。

## 7. 内容哈希与 NewsItem.id

内容哈希不得使用 Python 内置 `hash()`。后续实现必须按以下 `news-content-v1` 规则生成
确定性的 SHA-256：

1. `title` 和 `summary` 使用第 4 节规范化后的最终标准字符串；`summary` 缺失时是空字符串；
2. `content` 当前严格为 `None`；
3. 字段顺序严格为 `title`、`summary`、`content`；
4. 字节载荷先写入 ASCII 前缀 `news-content-v1` 后跟单个 NUL 字节；
5. 对每个字段依次编码：文本写入 ASCII `S`，再写入其 UTF-8 字节长度的 8 字节无符号
   大端整数，再写入 UTF-8 字节；`None` 只写入 ASCII `N`；
6. 对完整载荷计算 SHA-256，`content_hash` 保存 64 位小写十六进制摘要；
7. 当前 `NewsItem.id` 保存 `eastmoney:` 与该摘要的直接拼接。

离线固定测试向量：`title="SYNTHETIC TITLE"`、
`summary="SYNTHETIC SUMMARY"`、`content=None` 的摘要为
`c59a9bde03672412d23004c61f5414ceba5e3529721c10bd25b52b71a11b3474`。

长度前缀和 `None` 标记用于消除字段边界歧义。来源不进入内容哈希，因为 Repository 已按
`source` 作用域使用 `(source, content_hash)` 作为无 external ID 时的幂等身份；URL、
发布时间、抓取时间、请求证券和文章来源也不进入内容哈希，避免非内容元数据变化破坏
相同内容去重。该规则只做精确内容去重，不承担近似转载或跨来源事件聚类。

## 8. URL 与 article code 边界

AkShare `1.18.46` 的 N2 静态源码表明：Eastmoney JSON 记录包含内部 `code`，AkShare 用
该值形成公开 `新闻链接`；随后 `code` 被重命名为占位列并从最终 DataFrame 字段集合移除。
因此必须区分：

- 上游内部 article code 存在：N2 已确认；
- article code 的真实格式、唯一性和稳定性：未由 N3/N4 确认；
- AkShare 最终公开返回是否保留 article code：不保留；
- 当前未来 Transport 边界是否可使用 article code：不可使用；
- `external_id`：固定 `None`，不得从 URL、标题、行号或数组下标恢复；
- `url`：只读取 Transport 已明确提供的 `新闻链接`；项目代码不得复制 AkShare 的拼接规则。

若 P2-02 未来选择绕过 AkShare 公开 DataFrame、直接消费 JSON/JSONP，则已经扩大 Transport
边界，必须先修订本文、增加脱敏 Fixture，并独立证明 article code 和 URL 的语义；不能以
本轮静态发现为由直接把内部 `code` 暴露为 external ID。

## 9. Parser/Provider 结果语义

操作标识固定为 `fetch_news`；成功请求返回
[`ProviderResult`](../daily_report_agent/providers/contracts.py)，请求级失败抛出现有
[`ProviderError`](../daily_report_agent/providers/errors.py) 子类。

| 场景 | 后续行为 |
|---|---|
| 请求成功且无记录 | 返回空 `items`，不是异常；产生安全 `INFO/MISSING_DATA` Issue，code=`news_not_found` |
| 单条缺失摘要、URL 或其他非关键字段 | 保留合法记录，按字段产生或聚合安全 `DataIssue`；不得在 Issue 中放标题、URL 或响应片段 |
| 单条缺少标题 | 跳过该条，产生安全 `WARNING/PARSE` Issue，code=`missing_news_title` |
| 单条发布时间无法安全解释 | 跳过该条，产生安全 `WARNING/PARSE` Issue，code=`invalid_published_at` |
| 部分记录损坏 | 保留全部合法记录并返回 Issues；坏记录不能使合法记录丢失 |
| 顶层响应结构无法识别 | 抛 `ProviderParseError`，code=`invalid_news_response` |
| 市场或证券输入不支持 | 抛 `ProviderValidationError`，code=`unsupported_market` 或 `invalid_symbol` |
| 网络或超时 | 映射为 `ProviderNetworkError` 或 `ProviderTimeoutError` |
| 限流 | 映射为 `ProviderRateLimitError` |
| 阻断或不可用 | 映射为 `ProviderBlockedError` 或 `ProviderUnavailableError` |

当前记录级安全 Issue 固定为：

- `invalid_news_record`、`missing_news_title`、`invalid_published_at`：
  `WARNING/PARSE`，跳过坏记录；
- `published_time_date_only`：`WARNING/PARSE`，保留记录但明确只有日期精度；
- `missing_news_summary`：`WARNING/MISSING_DATA`，保留记录且 `summary=""`；
- `missing_news_url`、`invalid_news_url`：`WARNING/MISSING_DATA`，保留记录且
  `url=None`；
- `news_not_found`：`INFO/MISSING_DATA`，表示 Transport 空结果或过滤后为空，不是失败。

安全错误和 Issue 只能包含 Provider ID、operation、安全 code、字段名、数量和可公开的错误
类别。不得包含底层异常文本、traceback、完整 URL、查询参数、请求头、Cookie、Token、
响应正文或真实新闻内容。底层异常只通过异常链保留。`DataIssue` 用于成功响应中的空结果、
记录级跳过和字段质量，不得把请求级失败伪装为成功 Issue。

### 9.1 时间窗口、排序和 limit

Provider 先让 Parser 解析所有行，再按以下顺序处理：

1. 使用闭区间 `since <= published_at <= until` 过滤；
2. 窗口外合法记录不返回，也不因此产生解析 Issue；
3. 窗口内记录按 `published_at` 降序排列；
4. 相同发布时间保持原始响应顺序；
5. 最后应用正整数 `limit`；较小 limit 不触发分页、重试或第二次 Transport 调用；
6. 最终没有 item 时返回成功空结果和安全 `news_not_found` Issue。

`since`、`until` 必须 aware 且 `since <= until`；不同时区按它们表示的真实时刻比较。
非法证券、时间窗口、limit 或 clock 均在调用 Transport 前失败。每次合法请求只调用 clock
一次、Transport 最多一次，同次合法 item 共用一个 `fetched_at`。

## 10. 离线实现状态与后续证据门禁

P2-02、P2-03、P2-04S 和 P2-05 已完成：

- [`news_transport.py`](../daily_report_agent/providers/eastmoney/news_transport.py) 只定义
  同步只读、显式注入的公开行 Protocol，没有默认或在线实现；
- [`news_parser.py`](../daily_report_agent/providers/eastmoney/news_parser.py) 不依赖
  AkShare 或 pandas，不联网、不读文件，只解析传入 tuple 行；
- [`news.py`](../daily_report_agent/providers/eastmoney/news.py) 负责请求验证、单次 clock、
  单次 Transport、安全错误映射、窗口、排序和 limit；
- `news_synthetic_multiple.json` 由测试代码加载，包含正常、缺字段、仅日期、窗口外和相同
  时间的虚构记录；正式 Parser 不读取 Fixture 文件；
- 导入、构造、正常/空/部分坏记录、时间、哈希、窗口、排序、limit 和错误映射均有纯离线
  测试。

### 10.1 P2-05 离线存储契约验证

[`test_eastmoney_news_storage.py`](../tests/integration/storage/test_eastmoney_news_storage.py)
只使用 synthetic Fixture 和 pytest 临时 SQLite 数据库，验证现有链路
`EastmoneyNewsProvider -> ProviderResult[NewsItem] -> SecurityRepository ->
NewsRepository`，没有新增生产存储编排：

- 首次写入 5 条不同内容哈希的新闻时，`insert_or_get_news()` 均返回
  `(Provider 生成的 NewsItem.id, True)`；数据库最终有 1 条证券、5 条新闻和 5 条证券
  关联；
- 当前 `external_id=None`，所以身份严格使用 `(source, content_hash)`。用不同 clock 和
  不同 URL 重放相同文本时，第二次 5 条均返回既有 ID 和 `inserted=False`，新闻与关联
  数量仍为 5；现有 insert-or-get 语义保留第一次存储的 `fetched_at` 和 URL，不执行更新；
- 每条关联都指向请求证券 `cn/123456`，默认 `relation_type="mentioned"`、
  `confidence=NULL`；重复建立同一关联不会增加行数；
- `get_by_id()` 返回标准 `NewsItem`，关联代码为 `("123456",)`；`published_at` 和
  `fetched_at` 均按现有 serializer 往返为 aware UTC。`DataIssue` 只留在
  `ProviderResult.issues`，不会混入新闻字段；
- `source="eastmoney"`、`source_type="news"`、`language="zh"` 保持不变；
  `external_id`、`content`、`source_reliability` 和 `raw_response_id` 均为 `NULL`；测试从未
  构造 `RawResponseRepository`，`raw_responses` 始终为 0；
- Provider 超时发生在事务打开前时，证券、新闻、关联和 raw response 四张表均保持 0；
  Provider 成功后若同一事务内的后续 Repository 外键操作失败，则此前的证券、新闻和合法
  关联全部回滚，四张表也均为 0；
- 测试同时核对既有两组新闻唯一索引、新闻主键、可空 `external_id/raw_response_id`、关联
  复合主键和外键约束；没有修改 migration、Repository、Pipeline、正式配置或默认路由。

以上只证明当前离线输入与现有存储契约可以安全组合，不证明在线 Transport、真实响应、
observed Fixture 或生产存储编排可用，也不授权写入 `provider_calls` 或 `raw_responses`。

P2-04 总项仍未完成，后续至少仍需：

1. 用受控真实响应制作最小脱敏 observed Fixture，固定顶层结构、字段类型、空值、
   时间格式、标题高亮、摘要长度、URL 形态、article code 和空/坏记录行为；
2. 验证函数说明与静态单页数量之间的实际行为，但不得用一次观察声明稳定 SLA；
3. 任一在线观察必须另行明确授权，限制证券、次数、重试和保留内容，并记录为在线证据。

当前 `external_id=None`，所以“重复 external ID”用例不适用于本 Provider；不得为测试伪造
external ID。Eastmoney 的 P2-05 只验证稳定内容哈希身份；通用 Repository 对非空
external ID 的幂等行为由独立 Repository 单元测试覆盖，不能据此宣称 Eastmoney 已暴露
external ID。

observed Fixture 必须手工脱敏，不保存 Cookie、Token、完整请求 URL、完整请求头、查询参数
或未脱敏响应正文。当前只有 synthetic Fixture，没有 observed Fixture，也没有调用新闻
函数或执行在线验证。

## 11. 当前明确不做

- 不实现在线 Transport、Fixture loader 生产能力或正式配置；
- 不调用 `stock_news_em()`，不发起网络请求，不执行第三方 Markdown 中的代码；
- 不修改 `main.py`、正式 DataSource、Pipeline、Analyzer、Prompt、Report 或 Notifier；
- 不新增数据库 migration，不写 `provider_calls` 或 `raw_responses`；
- 不启用 storage、腾讯 Shadow 或任何生产/备用路由；
- 不访问腾讯 B4 观察库；
- 不把 N2 静态源码核验或 synthetic 测试描述为在线可用性、真实响应或字段稳定性验证；
- 不进入公告、交易所披露、市场快讯、研报、多源去重或正式业务接入。
