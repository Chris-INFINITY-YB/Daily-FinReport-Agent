# B4 观测并行开发与合并门禁计划

更新时间：2026-07-27
适用范围：腾讯 QuoteProvider 阶段 2-B4 及合并候选门禁完成后的暂停与后续开发边界

## 1. 结论与执行原则

阶段 2-B4 不作为全部开发工作的阻塞项。项目可以在保持腾讯 Shadow 隔离的前提下，
并行推进 Profile Provider、News Provider 的离线能力和工程保障。

并行开发以及 B4 五日证据通过都不等于腾讯 QuoteProvider 已获准正式接入。以下门禁继续
保持不变：

- `storage.enabled` 默认保持 `false`；
- `providers.tencent_quote.shadow_enabled` 默认保持 `false`；
- 腾讯数据不得进入 Analyzer、Prompt、Report 或通知；
- 腾讯不得替换 AkShare，也不得被声明为正式或备用行情源；
- 不基于单次在线成功直接设计或启用正式路由、自动重试、降级和熔断；
- 已完成的 B4 证据继续保存在同一个项目外持久观察库中，不得迁移、覆盖或重跑。

## 2. 开发前基线

2026-07-18 的开发起点和 2026-07-24 的 B4 验收状态如下：

| 项目 | 基线 |
|---|---|
| 当前分支 | `main`，与 `origin/main` 一致 |
| 当前提交 | `c7b4a5ccc35c3d603274c112dfba2049a90e1fe2` |
| 腾讯 Shadow 实现提交 | `98229c67e1a33f83260684b081c7097d92c76c03` |
| 离线测试 | `282 passed` |
| `git diff --check` | 通过 |
| B4 ProviderCall | 5 |
| B4 MarketSnapshot | 15 |
| B4 PipelineRun | 5 |
| B4 RawResponse | 0 |
| B4 状态 | 五日证据、候选完整离线回归及同候选受控在线验证均通过 |
| 合并候选 | `b921a8e8a541551e19af069666d9be3edba2fa3d` |
| 当前本地测试 | P4-02 分支 Python 3.10/3.13 均为 `453 passed` |
| 当前远端门禁 | P4-01R 已合并；合并后自动与手动 Python 3.10/3.13 Job 均成功 |

回归保护值：

```text
完整数据 Prompt SHA-256:
7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3

固定日期 dry-run SHA-256:
8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0
```

开始每个开发任务前，应先确认工作区没有覆盖他人修改。B4 每日观测已经使用冻结基线完成；
并行开发仍不能改写观察证据。涉及 Shadow、存储 migration 或共享 Pipeline 的后续改动应
在独立工作树或独立分支中开发，并重新经过合并候选门禁。

## 3. 本轮允许开发的范围

### 3.1 可以直接开始

- 补全 ProfileProvider 契约测试；
- 实现 CN Profile Provider 的离线 Parser、字段映射和错误映射；
- 实现 News Provider 的离线 Parser、标准化、去重身份和错误映射；
- 增加脱敏 Fixture、契约测试、失败隔离测试和导入不联网测试；
- 完善 Provider 文档、CI、日志和开发验证命令；
- 开发不接入正式分析链路的证据字段与 Repository 测试。

### 3.2 可以设计和离线实现，但暂不启用

- Provider 选择策略接口；
- 缓存、限流、重试、熔断的抽象和纯离线状态测试；
- Profile/News Provider 的显式在线冒烟入口；
- 标准采集结果到未来分析输入的适配器。

这类工作必须保持默认关闭，不得修改现有 DataSource 的正式路由。

### 3.3 B4 证据通过后仍禁止

- 将腾讯 QuoteProvider 接入正式行情选择或降级链路；
- 用腾讯 `MarketSnapshot.pct_change` 代替 `PriceWindow.period_pct_change`；
- 修改 Analyzer、Prompt 或 Report 以消费腾讯 Shadow 数据；
- 自动打开 storage 或 Shadow 配置；
- 因开发或测试覆盖、删除、迁移 B4 观察库；
- 把五日低频受控成功写成“腾讯已正式可用”或生产 SLA 已达标。

### 3.4 已确认的实现约束

- `pyproject.toml` 当前显式列出 Python package；新增 Provider 子包时必须同步加入打包配置，
  并补充安装后导入测试；
- `SecurityProfile` 已有标准模型和 Protocol，但当前没有独立 Profile 存储表。B4 期间优先
  完成内存返回和离线契约，不夹带共享数据库 migration；
- `NewsItem`、`news_items`、`news_security_links` 和幂等 Repository 已具备，首个 News
  Provider 应复用这些契约，不另建平行新闻模型；
- 旧 `CNDataSource` 将行情、简介和新闻混在一次 `fetch()` 中，且共用一个 `error` 字段。
  新 Provider 不得复制这一耦合方式；
- 当前 Protocol-only 测试只覆盖 QuoteProvider 和 NewsProvider，ProfileProvider 是明确的
  首个低风险测试缺口；
- 腾讯 Shadow 与通用 storage、Pipeline 有共享面。涉及这些文件的重构应延后，或在独立
  分支中完成且不得用于 B4 每日观测。

## 4. 可执行任务清单

建议严格按任务编号推进。每个任务独立提交，当前一项验收通过后再开始下一项。

### P0：基线与隔离保护

- [x] **P0-01 建立开发分支或独立工作树（2026-07-18 已完成）**
  - 已从 `main` 基线提交 `c7b4a5ccc35c3d603274c112dfba2049a90e1fe2` 建立
    `codex/provider-parallel-development` 开发分支；
  - 分支起点已经记录；
  - B4 观测继续使用冻结基线，不使用开发中的工作目录。
- [x] **P0-02 执行开发前验证（2026-07-18 已完成）**
  - 完整离线测试通过；
  - `compileall` 通过；
  - 固定日期 dry-run 通过；
  - `git diff --check` 通过；
  - 两个回归哈希不变。
- [x] **P0-03 建立变更边界检查（2026-07-18 已建立，后续每次提交持续执行）**
  - 每次提交检查默认配置未打开；
  - 检查未修改腾讯正式准入状态；
  - 检查 Fixture 不包含 Cookie、Token、完整请求 URL 或未脱敏原始响应。
  - 勾选只表示检查机制已经建立；默认配置、腾讯准入状态和 Fixture 脱敏仍须在每次提交
    持续检查，不表示后续可以停止执行这些门禁。

验收：开发环境与 B4 观测环境相互独立，失败测试不会写入观察库。

### P1：补全 ProfileProvider 契约

- [x] **P1-01 补齐 Protocol 测试（2026-07-18 已完成）**
  - 将 `ProfileProvider` 纳入 Protocol-only 测试；
  - 验证返回类型为 `ProviderResult[SecurityProfile]` 的约定；
  - 验证空结果与请求失败语义严格区分。
- [x] **P1-02 固化 Profile 字段语义（2026-07-18 已完成）**
  - 字段契约、来源身份和证据门禁见
    [`cn_profile_provider_contract.md`](cn_profile_provider_contract.md)；
  - 为 `name`、`exchange`、`currency`、`industry`、`description` 建立字段证据表；
  - 未独立确认的字段返回 `None`，不得通过字符串拼接猜测；
  - `fetched_at` 必须为 timezone-aware；
  - `source` 必须使用稳定 Provider ID。
- [x] **P1-03 实现 CN Profile Provider 离线骨架（2026-07-18 已完成）**
  - [x] **P1-03A 来源归属离线验证（2026-07-18 已完成）**
    - 静态核验 AkShare `1.18.46` 的函数说明和 `eastmoney.com` 上游主机名；
    - 确认 AkShare 是调用库，原始来源 Provider ID 使用 `eastmoney`；
    - 只解除来源身份门禁，不代表响应结构、字段映射或 Provider 实现已验证；
    - 证据与限制见
      [`cn_profile_provider_contract.md`](cn_profile_provider_contract.md)。
  - [x] **P1-03B 实现离线骨架（2026-07-18 已完成）**
    - 网络访问通过显式注入的 Transport/Client 隔离；
    - 导入模块和构造 Provider 均不得联网；
    - Parser 只消费传入 Fixture；
    - 外部异常映射到现有安全 ProviderError，不泄露底层 URL 或凭据；
    - 如新增 Provider 子包，同步更新 `pyproject.toml` 的显式 package 列表。
- [ ] **P1-04 增加离线 Fixture 和契约测试**
  - [x] **P1-04R 固化 2026-07-18 受控观察失败记录（2026-07-18 已完成）**
    - 单次 `stock_individual_info_em(symbol="600519")` 调用失败，调用次数 `1`、自动重试 `0`；
    - 安全错误类别为“响应不可用”，异常类型为 `JSONDecodeError`；
    - 静态源码确认失败位于本地 `r.json()` 解析边界，早于 `item/value` 行记录形成；
    - 未保存原始响应或 HTTP 诊断内容，未创建 observed Fixture；
    - 失败记录见 [`cn_profile_provider_contract.md`](cn_profile_provider_contract.md)；
    - 本记录不完成 P1-04；任何后续尝试必须作为新的受控观察，不能冒充本次继续执行。
  - [x] **P1-04R2 固化 2026-07-25 独立受控观察失败记录（2026-07-25 已完成）**
    - 联网前重新核对 AkShare `1.18.46`、目标源码 SHA-256 和单请求、无重试/分页边界；
    - 项目外一次性脚本先通过 synthetic 脱敏自检，再固定调用
      `stock_individual_info_em(symbol="600519", timeout=10.0)`；
    - 逻辑调用 `1`、底层请求 `1`、自动重试 `0`、并发 `0`，安全错误类别为
      `json_decode`，异常类型为 `JSONDecodeError`；
    - 未保存原始响应、完整 DataFrame、URL、请求参数或底层异常正文，未创建 observed
      Fixture；
    - 本次是单独授权的新观察，不是 P1-04R 的重试；失败记录见
      [`cn_profile_provider_contract.md`](cn_profile_provider_contract.md)；
    - `name` 和 `industry` 仍为 E1，P1-04 继续未完成，未来观察仍须单独授权。
  - [x] **P1-04R3 固化 2026-07-27 独立受控观察失败记录（2026-07-27 已完成）**
    - 联网前确认 AkShare `1.18.46`、目标源码 SHA-256，以及单请求、无重试/循环/分页
      边界均未变化；
    - 项目外一次性程序通过 synthetic 脱敏自检后，固定调用
      `stock_individual_info_em(symbol="600519", timeout=10.0)`；
    - 逻辑调用 `1`、底层 HTTP 请求 `1`、自动重试 `0`、并发 `0`、redirect `0`，
      墙钟耗时 `7032 ms`；
    - 安全 HTTP 事实为 status `502`、规范化 Content-Type `text/html`；随后在本地
      `r.json()` 边界发生 `JSONDecodeError`，安全错误类别为 `json_decode`；
    - 未形成 `item/value` 行记录，未保存正文、完整 DataFrame、URL、请求参数或异常
      正文，未创建 observed Fixture；
    - 本次不是前两次请求的继续或重试；失败记录见
      [`cn_profile_provider_contract.md`](cn_profile_provider_contract.md)；
    - `name` 和 `industry` 仍为 E1，P1-04 继续未完成。连续第三次未形成 JSON/行记录，
      停止相同入口的后续在线尝试；下一任务改为离线评估替代来源或替代 Transport，
      不发起第四次相同请求。
  - [x] **P1-04A CN Profile 替代来源或 Transport 静态可行性评估
    （2026-07-27 已完成，推荐待批准）**
    - 只读取官方公开页面、本机 AkShare `1.18.46` 包元数据和静态源码；没有调用
      Eastmoney、CNInfo、交易所、Tushare、Xueqiu 或其他 Provider 数据接口；
    - 已评估 Eastmoney 同源替代边界、上交所/深交所/北交所、CNInfo、Tushare Pro 和
      Xueqiu，并记录原始发布身份、字段、响应、许可、离线性、Fixture 与维护风险；
    - Proposed 结论为建立独立 `cninfo` Profile Provider，与冻结在线方向的
      `eastmoney` 骨架并存；该结论仍需用户架构批准，不表示 CNInfo 内部接口可自动访问
      或在线可用；
    - 当前不采用交易所分片方案，因为 `Security.exchange` 没有足够的来源证明、封闭
      词表和新旧代码映射，且三家交易所字段与响应不对称；
    - Tushare 需要账号、Token 和积分/可能付费，数据服务许可限制个人、不可转让和
      非商业使用；Xueqiu 条款限制未经授权的自动化抓取，因此均不是当前默认首选；
    - 完整 ADR、证据日期、源码相对路径及 SHA-256 见
      [`cn_profile_source_alternatives.md`](cn_profile_source_alternatives.md)；
    - P1-04 继续未完成，`name`、`industry` 继续为 E1，未创建 Fixture、未实现新
      Provider 或在线 Transport。
  - [ ] **P1-04B CNInfo CN Profile 离线 Provider 契约与 synthetic Fixture
    （等待用户批准）**
    - 仅在用户批准 `cninfo` Provider ID 和 P1-04A Proposed 决策后开始；
    - 新建独立 Descriptor、Transport Protocol、纯 Parser 和显式依赖注入 Provider，
      只消费本地 synthetic 零/一行宽表记录；
    - 不导入/执行 AkShare，不实现在线 Transport，不进入正式 DataSource、配置、路由、
      Analyzer、Prompt、Report、通知或数据库；
    - observed Fixture 另需先确认自动化访问与最小脱敏保存边界，再通过单独任务、
      单独在线授权取得；E3 和正式路由门禁保持不变。
  - 正常资料；
  - 部分字段缺失；
  - 空响应；
  - 字段名或结构异常；
  - 上游异常与敏感错误文本脱敏；
  - 市场不匹配和非法证券输入。
- [x] **P1-05 增加显式离线验收入口或最小示例（2026-07-18 已完成）**
  - 新增始终离线的 `scripts/eastmoney_profile_offline_check.py`；
  - 只接受显式本地 synthetic JSON Fixture，不提供 `--allow-network` 或在线 Transport；
  - 通过 Fixture Transport 调用正式 Provider、Parser 和标准模型，不绕过验收链；
  - 不读取正式配置，不写数据库，不进入 Analyzer、Report 和通知；
  - 合成 Fixture 不提升字段证据等级，P1-04 仍保持未完成；
  - 使用方法与边界见 [`cn_profile_provider_contract.md`](cn_profile_provider_contract.md)。

阶段性说明（更新于 2026-07-27）：P1 的离线 Provider 骨架、Protocol/字段契约和显式
离线验收入口已经完成，但 P1 整体尚未完成。P1-04 仍等待由真实响应制作的最小脱敏
Fixture；
2026-07-18、2026-07-25 和 2026-07-27 三次各自授权、各自计数的受控请求均在本地
`r.json()` 解析边界失败，没有形成 `item/value` 行记录。相同入口的后续在线观察现已
停止。P1-04A 已完成纯离线替代来源评估，Proposed 建议是建立独立 `cninfo` Profile
Provider；P1-04B 等待用户架构批准，且只允许先做离线契约和 synthetic Fixture。
该证据缺口不阻塞 P2 的纯离线开发，但 Eastmoney Profile 仍不得被声明为在线可用，也
不得进入正式 DataSource 路由。

验收：Profile Provider 可完全通过 Fixture 验证；旧 DataSource、腾讯 Shadow、Prompt 和
报告输出无变化。

### P2：实现第一个标准 News Provider 的离线能力

- [x] **P2-01 选择并记录首个公司新闻来源（2026-07-18 已完成）**
  - 优先迁移当前旧链路已经使用的公司新闻能力；
  - 明确它只负责公司新闻，不冒充公告或市场快讯；
  - 固化 Provider ID、`source_type`、时间口径、URL 和 external ID 证据。
  - 首个来源固定为 Eastmoney，Provider ID 为 `eastmoney`，市场为 `cn`，
    `source_type` 固定为 `news`；AkShare `stock_news_em` 只是旧链路调用入口；
  - 业务范围仅为按六位 ASCII 数字证券代码查询的 A 股个股 company-related news，
    不表示公告、交易所披露、市场快讯或研报；
  - AkShare `1.18.46` 静态源码确认 article code 存在于内部响应并用于形成公开链接，但
    最终 DataFrame 不保留该 code，因此当前 `external_id=None`，不得从链接或行号反推；
  - 只消费 Transport 明确提供的公开链接，不在项目内复制或猜测链接拼接规则；
  - 无时区新闻时间后续按 `Asia/Shanghai` 解释，精确原始格式仍等待 observed Fixture；
  - 字段、哈希、结果语义及证据缺口见
    [`cn_news_provider_contract.md`](cn_news_provider_contract.md)。

P2-01 完成时只包含固定版本第三方源码的静态证据核验和离线契约设计，没有调用新闻函数、
进行在线验证或制作 observed Fixture。后续离线实现状态见下列任务；P2-01 本身不授权进入
正式 DataSource、Analyzer、Prompt、Report、通知或任何生产/备用路由。

- [x] **P2-02 实现 News Transport 与 Parser 分离（2026-07-18 已完成）**
  - Transport 只负责获取响应；
  - Parser 只负责解析传入内容；
  - Provider 负责证券、时间窗口、limit、标准错误和质量 Issue；
  - 导入和构造阶段不得联网。
- [x] **P2-03 映射标准 NewsItem（2026-07-18 已完成）**
  - `published_at`、`fetched_at` 必须带时区；
  - 保留来源、原文 URL、external ID 和关联证券；
  - 使用稳定内容哈希；
  - 空摘要使用空字符串，未知正文使用 `None`；
  - 不把网页正文或新闻内容当作程序指令。
- [ ] **P2-04 增加离线 Fixture 和契约测试**
  - [x] **P2-04S synthetic Fixture 与离线契约测试（2026-07-18 已完成）**
    - 已覆盖正常、空结果、部分坏记录、缺摘要/URL、仅日期、非法时间、稳定哈希、
      时间窗口、稳定排序、limit、导入/构造隔离及安全错误映射；
    - synthetic Fixture 只验证离线契约，不提升 N2/N3/N4 证据等级；
    - 当前 `external_id=None`，不得伪造重复 external ID；重复身份只验证稳定内容哈希；
  - P2-04 总项仍等待 observed Fixture、真实时间格式、真实字段类型、article code 和实际
    返回行为证据；这些缺口不阻塞纯离线骨架，但阻止在线可用声明；
  - 正常多条新闻；
  - 重复 external ID（当前 Provider 不适用，留待未来取得真实 external ID 后验证）；
  - 无 external ID 时的内容哈希去重；
  - 缺 URL、缺摘要、缺正文；
  - 非法发布时间；
  - 空结果与部分坏记录；
  - 限流、网络、解析和不可用异常映射。
- [x] **P2-05 验证现有存储契约（2026-07-18 已完成）**
  - synthetic Provider 结果通过现有 `SecurityRepository` 和 `NewsRepository` 写入 pytest
    临时 SQLite；首次 5 条均插入，使用不同 `fetched_at` 和 URL 重放相同内容时第二次 5 条
    均复用既有 ID，最终仍为 1 条证券、5 条新闻和 5 条关联；
  - 当前 Eastmoney `external_id=None`，身份使用 `(source, content_hash)`；insert-or-get
    保留第一次存储的 `fetched_at` 和 URL，不把第二次获取误作更新；
  - `news_security_links` 只关联请求证券，重复链接幂等，默认
    `relation_type="mentioned"`、`confidence=NULL`；
  - Repository 往返返回标准 `NewsItem`，时间为 aware UTC；DataIssue 不写入新闻字段，
    `raw_response_id=NULL` 且 `raw_responses` 始终为 0；
  - Provider 在事务前失败时四张相关表均为空；同一事务内后续 Repository 外键失败时，
    已插入的证券、新闻和关联全部回滚；
  - 只新增离线集成测试和文档；没有修改 migration、Repository、Pipeline、正式配置、
    默认路由或生产存储编排。

验收：至少一个 News Provider 可以在纯离线条件下产生标准 `NewsItem`，重复输入不会重复
入库，但仍不接入正式 Analyzer、Prompt、Report 或通知。

阶段性说明（2026-07-18）：P2-02、P2-03、P2-04S 和 P2-05 已完成；没有在线 Transport、
在线请求或 observed Fixture。P2-04 总项仍未完成，因此 P2 阶段整体尚未完成，也不得开始
P3。当前实现没有接入正式 DataSource、Pipeline、Analyzer、Prompt、Report、通知、生产
storage 或 Provider 编排。

### P3：第二新闻来源与公告边界

依赖：P2 完成。

- [ ] **P3-01 接入第二个可切换的公司新闻来源（离线优先）**
  - 使用独立 Provider ID 和独立 Fixture；
  - 不复用第一个来源的字段假设；
  - 验证单一来源失败不影响另一来源。
- [ ] **P3-02 建立公告独立类型**
  - 公告 Provider 与公司新闻 Provider 分开；
  - `source_type` 明确区分公告、公司新闻和市场快讯；
  - 保留公告原文 URL、发布时间和证券关联。
- [ ] **P3-03 增加多源去重的纯函数层**
  - 先支持 external ID 和精确内容哈希；
  - 近似去重与事件聚类另立任务，不混入 Provider Parser；
  - 保留各来源证据，不因去重丢失来源链。

验收：两个公司新闻来源可以独立运行和切换；公告不会被混入普通新闻语义。

### P4：工程保障

P4 可与 P1～P3 穿插，但每项应单独提交。

- [x] **P4-00 仓库生成物治理（2026-07-25 已完成）**
  - 提交：`cf461395b3c4e5f6938d5b9609188720cad6225f`；
  - 删除历史误提交的 Python bytecode，版本控制中不再包含 `.pyc`；
  - `.gitignore` 统一忽略 `__pycache__`、`*.py[cod]` 和常见测试/分析缓存；
  - Python 3.10/3.13 完整测试后生成的缓存不再污染 Git 工作区。
- [x] **P4-01 CI 基线（2026-07-25 已完成）**
  - 提交：`3d5bfdd4056b30ddc56ed04ca89a67968015bb8d`；
  - `.github/workflows/offline-ci.yml` 在 Python 3.10 和 3.13 运行完整离线测试；
  - 普通 pytest 继续由全局 autouse Fixture 禁止 socket 和 urllib 网络访问；
  - 执行 `compileall`、`git diff --check` 和测试后工作区检查，bytecode 输出重定向到
    runner 临时目录；
  - 仅安装 `.[test]`，不安装 online extra，不要求或注入真实 API Key。
  - P4-01R 验收提交：`b7fa7386b211579aaa1999f415acc9da07436119`；
  - 远程状态：2026-07-26 的 GitHub Actions
    [`30193535946`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30193535946)
    已成功创建 Python 3.10/3.13 两个 Job，均为 `407 passed`，compileall 和工作区清洁
    门禁同时通过；P4-01 已由本地完成升级为远端门禁通过。
  - P4-01R 已通过 merge commit
    `2eeec791a0494b78359d67dd4e0875e81d138cd7` 进入 `main`；合并后自动运行
    [`30249579799`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30249579799)
    和手动复核运行
    [`30249744777`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30249744777)
    的 Python 3.10/3.13 Job 均成功。
- [x] **P4-02 Provider 指标与安全日志设计（2026-07-27 已完成）**
  - 只记录 Provider ID、operation、状态、耗时、数量和安全错误码；
  - 不记录 Token、Cookie、完整 URL、真实响应正文；
  - 日志失败不得改变正式 Pipeline 状态。
  - 新增冻结、slots 化的 `ProviderMetricEvent`，公开字段严格固定为
    `provider_id`、`operation`、`status`、`duration_ms`、`item_count`、
    `issue_count`、`retry_count`、`error_code`；
  - 单行 JSON 日志字段顺序固定，只能从已验证事件生成，普通输出失败被隔离，
    `KeyboardInterrupt` 和 `SystemExit` 不被吞掉；
  - 当前只接入腾讯 Quote Shadow 编排边界；默认关闭和 dry-run 路径不加载在线
    Transport，也不产生指标事件；
  - ProviderCall 状态、请求指纹、Snapshot 持久化和正式 PipelineRun 语义保持不变；
  - 详细设计与限制见
    [`provider_metrics_and_safe_logging.md`](provider_metrics_and_safe_logging.md)；
  - 实现 HEAD `29233c22155bf6ecf2c5b3ff32c12942cacbfc70` 的 GitHub Actions
    push 运行
    [`30251950437`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30251950437)
    和 pull_request 运行
    [`30252485316`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30252485316)
    均成功创建 Python 3.10/3.13 Job，四个 Job 的 pytest、compileall、diff 及工作区
    清洁步骤全部通过；
  - Draft PR
    [`#3`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/pull/3)
    保持 Draft，未合并。
- [x] **P4-03 当前进度文档同步（2026-07-25 已完成）**
  - 每完成一个 Provider，更新字段证据、限制和真实验证状态；
  - 明确区分“离线实现完成”“单次在线验证”“连续观测验收”；
  - README 只描述已发生且可复现的结果。
  - 本轮已同步 B4 候选门禁、P1-04R2、P4-00/P4-01、`407 passed` 和暂停状态；
  - 后续开发恢复后，文档同步仍作为每个任务的持续完成条件。

验收：新贡献者无需外部服务即可安装、运行测试并理解 Provider 的安全边界。

## 5. 每个任务的完成定义

任务只有同时满足以下条件才可标记完成：

1. 代码、类型和文档的语义一致；
2. 正常、空结果、部分结果和失败路径均有离线测试；
3. 导入、构造、pytest 和 dry-run 均不联网；
4. 新增 Provider 有稳定 ID、能力、市场范围和字段证据；
5. 错误信息不泄露凭据、完整 URL 或原始响应；
6. 默认配置、正式 DataSource 路由和腾讯 Shadow 状态不变；
7. 既有测试加新增测试全部通过；
8. `compileall`、dry-run、`git diff --check` 通过；
9. Prompt 和固定日期 dry-run 回归哈希保持不变；
10. 变更已按单一职责提交，能够独立回退。

## 6. 建议实施顺序

```text
P0 基线隔离
  → P1 ProfileProvider 离线实现
  → P2 首个 News Provider 离线实现
  → P3 第二新闻来源与公告边界
  → Provider 编排、正式路由与分析接入（另行验收后）

B4 腾讯连续观测（已完成）
  → 2026-07-20 至 2026-07-24 五日证据通过
  → 合并候选 b921a8e8 完整离线回归通过
  → 2026-07-25 同候选单次受控在线验证通过
  → 另立任务决定是否启动正式接入设计
```

推荐先执行 P0 和 P1。它们与腾讯 Shadow 的共享面最小，能够最快验证并行开发流程是否
稳定；P2 随后提供项目最关键的可追溯新闻证据能力。

## 7. B4 结束后的合并门禁

B4 五日证据和候选门禁均已通过，完整记录见
[`tencent_quote_b4_acceptance.md`](tencent_quote_b4_acceptance.md)。这不自动代表腾讯可以
进入正式链路。候选门禁状态如下：

- [x] 2026-07-20 至 2026-07-24 五个连续交易日记录完整；
- [x] 无 403/429、未解释的网络错误或协议漂移；
- [x] 无持续缺失证券或陈旧行情；
- [x] `raw_responses` 始终为 0；
- [x] 正式 Pipeline、报告和通知未受影响；
- [x] 并行开发没有改变观察期间的执行代码；
- [x] 在候选 `b921a8e8a541551e19af069666d9be3edba2fa3d` 上重新执行完整离线回归；
- [x] 2026-07-25 在单独授权后，对同一候选执行一次受控在线验证。

候选门禁通过只允许另立任务评审腾讯的正式路由、降级策略及业务接入，不代表这些能力
已经设计、验收或启用。P1-04、P2-04 和 P3 的状态不因 B4 完成而改变。

## 8. 当前远端验收状态

截至 2026-07-27，P4-00 与 P4-01 已完成本地验收，P4-01R 验收提交
`b7fa7386b211579aaa1999f415acc9da07436119` 的 Python 3.10/3.13 本地完整离线测试
各为 `407 passed`，工作区无 Python 生成物。GitHub Actions 运行
[`30193535946`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30193535946)
的两个矩阵 Job 也均为 `407 passed`，远端 compileall 与工作区清洁门禁通过。P4-01
已通过 merge commit `2eeec791a0494b78359d67dd4e0875e81d138cd7` 进入 `main`；合并后
自动运行 `30249579799` 和手动复核运行 `30249744777` 的 Python 3.10/3.13 Job 均成功。
P4-01 不再处于远端待验证状态，但该结果不扩大后续任务授权：

- 不再使用相同入口在线尝试 Eastmoney Profile；下一任务改为纯离线评估替代来源或替代
  Transport；该 P1-04A 评估现已完成，`cninfo` Proposed 建议仍待用户批准；
- 不启动 P2-04 在线观察或 P3 开发；
- 不设计或启用腾讯正式路由、降级、缓存、限流、重试或熔断；
- 不修改默认关闭配置；
- P1-04 仍未完成，任何 Provider 均未因本次 CI 验收而晋级。
