# B4 观测期间并行开发计划

更新时间：2026-07-18
适用范围：腾讯 QuoteProvider 阶段 2-B4 尚未验收期间的后续开发

## 1. 结论与执行原则

阶段 2-B4 不作为全部开发工作的阻塞项。项目可以在保持腾讯 Shadow 隔离的前提下，
并行推进 Profile Provider、News Provider 的离线能力和工程保障。

并行开发不等于腾讯 QuoteProvider 已完成验收。以下门禁在 B4 完成前保持不变：

- `storage.enabled` 默认保持 `false`；
- `providers.tencent_quote.shadow_enabled` 默认保持 `false`；
- 腾讯数据不得进入 Analyzer、Prompt、Report 或通知；
- 腾讯不得替换 AkShare，也不得被声明为正式或备用行情源；
- 不基于单次在线成功直接设计或启用正式路由、自动重试、降级和熔断；
- B4 继续使用同一个项目外持久观察库，每个确认的交易日最多执行一次。

## 2. 开发前基线

2026-07-18 已完成以下只读核对：

| 项目 | 基线 |
|---|---|
| 当前分支 | `main`，与 `origin/main` 一致 |
| 当前提交 | `c7b4a5ccc35c3d603274c112dfba2049a90e1fe2` |
| 腾讯 Shadow 实现提交 | `98229c67e1a33f83260684b081c7097d92c76c03` |
| 离线测试 | `282 passed` |
| `git diff --check` | 通过 |
| B4 ProviderCall | 0 |
| B4 MarketSnapshot | 0 |
| B4 PipelineRun | 0 |
| B4 状态 | Day 0，尚未开始连续交易日观测 |

回归保护值：

```text
完整数据 Prompt SHA-256:
7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3

固定日期 dry-run SHA-256:
8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0
```

开始每个开发任务前，应先确认工作区没有覆盖他人修改。B4 的每日观测应始终使用已确认
的冻结基线；若并行开发会修改 Shadow、存储 migration 或共享 Pipeline，应在独立工作树
或独立分支中开发，不能用未验收代码执行 B4。

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

### 3.3 B4 验收前禁止

- 将腾讯 QuoteProvider 接入正式行情选择或降级链路；
- 用腾讯 `MarketSnapshot.pct_change` 代替 `PriceWindow.period_pct_change`；
- 修改 Analyzer、Prompt 或 Report 以消费腾讯 Shadow 数据；
- 自动打开 storage 或 Shadow 配置；
- 因开发或测试覆盖、删除、迁移 B4 观察库；
- 把一次或少量在线成功写成“腾讯已正式可用”。

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

阶段性说明（2026-07-18）：P1 的离线 Provider 骨架、Protocol/字段契约和显式离线验收
入口已经完成，但 P1 整体尚未完成。P1-04 仍等待由真实响应制作的最小脱敏 Fixture；
2026-07-18 的一次受控请求在本地 `r.json()` 解析边界失败，没有形成 `item/value` 行记录。
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
- [ ] **P2-05 验证现有存储契约**
  - `NewsRepository.insert_or_get_news()` 保持幂等；
  - `news_security_links` 正确建立关联；
  - Provider 失败不提交半成品事务；
  - 不保存未经脱敏的 raw response。

验收：至少一个 News Provider 可以在纯离线条件下产生标准 `NewsItem`，重复输入不会重复
入库，但仍不接入正式 Analyzer、Prompt、Report 或通知。

阶段性说明（2026-07-18）：P2-02、P2-03 和 P2-04S 已完成；没有在线 Transport、在线
请求或 observed Fixture。P2-04 总项与 P2-05 均未完成，当前实现没有接入正式 DataSource、
Pipeline、Analyzer、Prompt、Report、通知、storage 或 Provider 编排。

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

- [ ] **P4-01 CI 基线**
  - 在受支持 Python 版本运行离线测试；
  - 默认禁止网络；
  - 执行 `compileall` 和 `git diff --check`；
  - 不要求真实 API Key。
- [ ] **P4-02 Provider 指标与安全日志设计**
  - 只记录 Provider ID、operation、状态、耗时、数量和安全错误码；
  - 不记录 Token、Cookie、完整 URL、真实响应正文；
  - 日志失败不得改变正式 Pipeline 状态。
- [ ] **P4-03 文档同步**
  - 每完成一个 Provider，更新字段证据、限制和真实验证状态；
  - 明确区分“离线实现完成”“单次在线验证”“连续观测验收”；
  - README 只描述已发生且可复现的结果。

验收：新贡献者无需外部服务即可安装、运行测试并理解 Provider 的安全边界。

## 5. 每个任务的完成定义

任务只有同时满足以下条件才可标记完成：

1. 代码、类型和文档的语义一致；
2. 正常、空结果、部分结果和失败路径均有离线测试；
3. 导入、构造、pytest 和 dry-run 均不联网；
4. 新增 Provider 有稳定 ID、能力、市场范围和字段证据；
5. 错误信息不泄露凭据、完整 URL 或原始响应；
6. 默认配置、正式 DataSource 路由和腾讯 Shadow 状态不变；
7. 282 项既有测试加新增测试全部通过；
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

B4 腾讯连续观测与上述任务并行进行
  → 满足 5～7 个交易日证据
  → 单独验收
  → 再决定腾讯 QuoteProvider 是否晋级
```

推荐先执行 P0 和 P1。它们与腾讯 Shadow 的共享面最小，能够最快验证并行开发流程是否
稳定；P2 随后提供项目最关键的可追溯新闻证据能力。

## 7. B4 结束后的合并门禁

B4 完成后也不自动代表腾讯可以进入正式链路。合并或启用前仍需单独确认：

- 5～7 个交易日记录完整；
- 无 403/429、未解释的网络错误或协议漂移；
- 无持续缺失证券或陈旧行情；
- `raw_responses` 始终为 0；
- 正式 Pipeline、报告和通知未受影响；
- 并行开发没有改变观察期间的执行代码；
- 在合并后的候选提交上重新执行完整离线回归和一次受控验证。

只有上述验收通过后，才能另立任务设计腾讯的正式路由、降级策略及业务接入。
