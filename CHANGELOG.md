# Changelog

本文件记录用户可见功能、公开契约、运行方式、安全边界和重要验证基线的变化。项目目前
没有正式 Git release tag；CHANGELOG 从 2026-07-18 开始维护，当前变更统一归入
`Unreleased`。更早的开发过程可通过 Git 历史查询，当前能力与使用方法以
[`README.md`](README.md) 为准。

格式参考 Keep a Changelog，但在首次正式发布前不伪造版本号或发布日期。

## [Unreleased]

### Added

- 补齐 `ProfileProvider` Protocol 契约测试，固定标准 `Security` 输入、
  `ProviderResult[SecurityProfile]` 返回，以及成功空结果与请求失败的区别。
- 新增 Eastmoney CN Profile Provider 纯离线骨架：
  - 稳定 Provider ID 为 `eastmoney`，能力仅为 `PROFILE`，市场仅为 `cn`；
  - Transport 是同步只读 Protocol，Provider 必须显式注入 Transport；
  - Parser 只消费不可变行记录，不接受 DataFrame，也不承担网络、配置或持久化职责；
  - `symbol`、`market` 以请求 `Security` 为准，`source` 固定为 `eastmoney`；
  - `exchange`、`currency`、`description` 保持 `None`，动态市值和行情字段不会进入 Profile。
- 新增标准结果和错误语义：完整资料、部分资料、成功空结果、非法结构、非法证券和
  Transport 失败均有独立离线测试，安全错误不会外显底层 URL、Token 或异常正文。
- 新增始终离线的 synthetic Fixture 验收入口
  `scripts/eastmoney_profile_offline_check.py`：
  - 参数为 `--fixture`、`--symbol` 和可选 `--name`；
  - 通过 Fixture Transport 调用正式 `EastmoneyProfileProvider`、Parser 和标准模型；
  - 输出仅包含 Provider ID、执行状态、item/issue 数量、安全 Issue code 和字段存在性；
  - 退出码 `0` 表示 Provider 成功，`1` 表示 Provider/Parser 或内部失败，`2` 表示输入拒绝。
- 新增 `tests/fixtures/providers/eastmoney/profile_synthetic_minimal.json` 及目录说明。该样本
  明确为人工构造的 synthetic Fixture，不包含真实响应或动态行情值。
- 新增 CN Profile 字段契约与证据边界文档
  [`docs/cn_profile_provider_contract.md`](docs/cn_profile_provider_contract.md)，并建立 B4
  观察期间的并行开发计划
  [`docs/parallel_development_plan.md`](docs/parallel_development_plan.md)。
- 新增 P1-04A CN Profile 替代来源静态可行性 ADR
  [`docs/cn_profile_source_alternatives.md`](docs/cn_profile_source_alternatives.md)：
  - 比较 Eastmoney 同源替代边界、上交所、深交所、北交所、CNInfo、Tushare Pro 和
    Xueqiu 的来源身份、字段、响应、许可、离线性、Fixture 与维护风险；
  - 记录 AkShare `1.18.46` 候选模块相对路径、函数边界和 SHA-256，不记录本机绝对路径；
  - Proposed 建议建立独立 `cninfo` Profile Provider，与冻结在线方向的 `eastmoney`
    骨架并存；该建议等待用户批准且不表示在线可用；
  - 定义但不实施
    `P1-04B：CNInfo CN Profile 离线 Provider 契约与 synthetic Fixture`。
- 新增 Eastmoney CN 公司新闻纯离线 Provider：
  - 独立 `NEWS` Descriptor，Provider ID 为 `eastmoney`，市场为 `cn`，新闻类型为 `news`；
  - 同步只读 Transport Protocol、无 I/O 纯 Parser 和显式依赖注入 Provider；
  - 标准 `NewsItem` 映射、timezone-aware 时间、闭区间过滤、稳定排序和 `limit`；
  - `news-content-v1` SHA-256 内容身份；当前 `external_id=None`，不从 URL 或行号反推；
  - 空结果、部分坏记录、缺失摘要/URL、非法时间和 Transport 失败均有安全结果语义。
- 新增 `tests/fixtures/providers/eastmoney/news_synthetic_multiple.json`，覆盖多条新闻、缺摘要、
  缺 URL、仅日期、窗口外记录和相同发布时间；该样本明确为 synthetic，不是 observed
  Fixture 或真实响应副本。
- 新增 Eastmoney News 存储集成验证：首次向临时 SQLite 插入 5 条新闻和 5 条证券关联，
  重复执行新增 0 条；Provider 事务前失败无写入，事务内后续失败会完整回滚。
- 新增 CN News 字段、时间、URL、哈希和证据边界文档
  [`docs/cn_news_provider_contract.md`](docs/cn_news_provider_contract.md)。
- 新增腾讯 QuoteProvider
  [`B4 五日观测验收记录`](docs/tencent_quote_b4_acceptance.md)，只读固化 2026-07-20 至
  2026-07-24 的逐日结果、数据质量检查、审计清单、延迟统计、证据限制和未解除门禁。
- 新增 Python 3.10/3.13 离线 CI 基线，执行完整 pytest、`compileall`、
  `git diff --check` 和测试后工作区清洁检查；CI 不安装 online extra 或注入 API Key。
- 新增 Provider 安全指标与日志基础设施：
  - 冻结、slots 化事件严格限制为 Provider ID、operation、封闭终态、耗时、三个非负计数
    和安全错误码八个字段；
  - 固定字段顺序的单行 JSON 只从已验证事件生成，默认使用标准日志且不创建文件；
  - 普通输出失败不会改变 Provider、ProviderCall、Snapshot、Shadow 或正式 Pipeline
    结果，`KeyboardInterrupt` 和 `SystemExit` 仍向上传播；
  - 仅接入现有腾讯 Quote Shadow 编排边界，未接入 Eastmoney 或正式 Provider 路由。
- 新增 Provider 指标与安全日志设计文档
  [`docs/provider_metrics_and_safe_logging.md`](docs/provider_metrics_and_safe_logging.md)。

### Changed

- `daily_report_agent.providers` 顶层安全导出 Eastmoney Profile/News Descriptor、
  `EastmoneyProfileProvider` 和 `EastmoneyNewsProvider`。
- `pyproject.toml` 的显式 package 列表加入
  `daily_report_agent.providers.eastmoney`，并增加安装包导入回归测试。
- 开发准备状态完成校准：P0 分支隔离、离线验证和持续变更边界检查机制均已建立；这些
  门禁仍须在后续每次提交持续执行。
- README 更新当前节点、Eastmoney Profile/News 离线能力、synthetic 验收命令、项目路径、
  安装/验证命令和当前开发分支测试基线。
- 路线图将 P1-04A 标记为“静态评估完成、推荐待批准”，并明确 P1-04B 只有在用户批准
  `cninfo` Provider ID 后才能开始；P1-04、P2-04 和 P3 状态均未改变。
- 腾讯 Shadow 汇总 `main()` 支持注入 timezone-aware clock；CLI 默认仍使用当前 UTC，
  测试使用固定时间，从而消除固定 2026-07-14 Fixture 随系统日期移出 7 日窗口的问题。
  naive 或非 datetime clock 会被安全拒绝，7 日窗口和原有汇总兼容断言保持不变。
- B4 状态更新为五个连续交易日观测、合并候选完整离线回归及同候选单次受控在线验证均
  通过；这不表示腾讯已成为正式或备用行情源。
- 删除历史误提交的 Python bytecode，并忽略 `__pycache__`、`*.py[cod]` 及常见测试/
  分析缓存，双版本测试不再污染 Git 工作区。
- P4-01R 已完成 GitHub Python 3.10/3.13 远端离线门禁；后续 Provider、路由或在线
  观察仍须另立任务并单独授权。
- P4-01R 已通过 merge commit
  `2eeec791a0494b78359d67dd4e0875e81d138cd7` 进入 `main`；合并后自动运行
  `30249579799` 和手动复核运行 `30249744777` 的 Python 3.10/3.13 Job 均成功。
- 腾讯包的在线 Transport 类型改为兼容的惰性导出；公开名称保持不变，默认关闭、
  dry-run 和指标模块导入路径不会提前加载在线 Transport。

### Security

- Eastmoney Provider 模块导入和 Provider 构造不会加载 AkShare、pandas、requests 或任何
  在线客户端，也不会创建网络连接。
- 当前没有默认或生产在线 Transport；Eastmoney Provider 没有正式配置项，也未进入
  `CNDataSource`、Pipeline、Analyzer、Prompt、Report 或通知。
- 离线验收入口不提供 `--allow-network`，不读取 `.env`、`config.yaml` 或 watchlist，不
  打开 SQLite，不生成报告或通知。
- Fixture 加载只接受本地普通 JSON 文件，拒绝 URL、目录、标准输入、非法 JSON 和非对象
  行；错误输出不回显 Fixture 正文或完整路径内容。
- synthetic Fixture 不包含完整 URL、Cookie、Token、Header、请求日志或原始响应。
- Eastmoney News 不提供默认或在线 Transport，不读取正式配置，也未进入 DataSource、
  Pipeline、Analyzer、Prompt、Report、通知或 Provider 编排。
- 新闻标题、摘要、文章来源和 URL 始终作为不可信数据处理；Parser 不执行内容，也不根据
  内容猜测证券、来源或 external ID。
- News 存储验证不写入 `raw_responses`，所有 `news_items.raw_response_id` 均为 NULL。
- 腾讯仍为默认关闭的 Shadow Provider；`storage.enabled` 和
  `providers.tencent_quote.shadow_enabled` 均保持 `false`，B4 观测没有触发正式分析、
  报告、通知或行情路由。
- Provider 指标事件没有证券、价格、run ID、数据库路径、请求指纹、URL、Header、
  Cookie、Token、响应正文、异常正文、DataIssue message、details 或动态扩展字段。
- P4-02 全部新增测试均为纯离线测试；没有调用腾讯、Eastmoney 或其他 Provider 在线接口。
- P1-04A 只读取官方公开文档、本机包元数据和静态源码；没有调用 Eastmoney、CNInfo、
  交易所、Tushare、Xueqiu 或其他 Provider 数据接口，没有创建 Fixture、Provider 或
  在线 Transport。

### Validation

- P1-04A 文档链接、相对路径、证据日期和敏感模式检查通过；Python 3.10.20 与
  Python 3.13.9 完整离线测试均为 `453 passed`，双版本 compileall、普通/固定日期
  dry-run、固定哈希、离线 wheel 构建及双版本临时安装导入均通过。
- P1-04A wheel SHA-256 为
  `4c5de25767c404edc637879e67156ccb2eafeb4e0a99899097823fa144abd416`；
  `storage.enabled=false`、`providers.tencent_quote.shadow_enabled=false`，项目内生成物
  检查为 0。
- 当前完整离线测试：Python 3.10.20 和 Python 3.13.9 均为 `453 passed`。
- P4-02 定向离线测试：指标契约 `31 passed`，腾讯 Shadow `34 passed`，
  Provider contracts/errors `33 passed`，ProviderCall Repository `9 passed`。
- Python 3.10/3.13 `compileall` 均通过且缓存输出位于项目外；普通 dry-run、Prompt
  SHA-256、固定日期 dry-run SHA-256、离线 wheel 构建、双版本临时安装及导入均通过。
- P4-02 实现 HEAD `29233c22155bf6ecf2c5b3ff32c12942cacbfc70` 的 GitHub Actions
  push 运行 `30251950437` 和 pull_request 运行 `30252485316` 均通过 Python 3.10/3.13
  全部步骤；Draft PR `#3` 保持 Draft，未合并。
- P4-00 完成后版本控制及项目工作区中的 Python 生成物均为 0；P4-01 本地等价 CI 门禁
  通过。P4-01R 验收提交 `b7fa7386b211579aaa1999f415acc9da07436119` 已在
  2026-07-26 的 GitHub Actions
  [`30193535946`](https://github.com/Chris-INFINITY-YB/Daily-FinReport-Agent/actions/runs/30193535946)
  中通过 Python 3.10/3.13 两个 Job（均为 `407 passed`），由本地完成升级为远端门禁
  通过。
- Eastmoney Profile 相关测试：`41 passed`；其中离线验收入口测试：`12 passed`。
- Eastmoney News Provider、Parser、契约、存储及相关 Repository/事务测试：`83 passed`。
- `compileall`、固定日期 dry-run、README 中的 synthetic 离线命令和 `git diff --check`
  均已通过。
- B4 只读验收：5 个 `audit_manifest.sha256` 全部通过；数据库为 5 条 PipelineRun、
  5 条 ProviderCall、15 条 MarketSnapshot、0 条 RawResponse，三只固定证券各 5 条，
  无字段空值、非 CNY、重复业务键、孤立快照、缺失证券或错误信号。
- B4 合并候选 `b921a8e8a541551e19af069666d9be3edba2fa3d` 在 Python 3.10.20 和
  Python 3.13.9 的完整离线测试均为 `403 passed`；2026-07-25 同候选单次受控在线验证
  为一次逻辑调用、一次底层请求、请求/返回 3/3、`retry_count=0`、Issue 0，未出现
  403、429、timeout、网络或 Parser 异常。
- Prompt SHA-256 保持
  `7d532b4031a223ec12e888b9e4fa236e313dfc08e20fe0f47c8aa87a49cd9cc3`。
- 固定日期 dry-run SHA-256 保持
  `8069e90b2cb81d5530849de7ccb0b85e1070d8506258c4e5628375dbf8b539f0`。

### Known limitations

- P1-04 仍未完成：当前没有根据真实 Eastmoney 响应制作的最小脱敏 observed Fixture，
  `name` 和 `industry` 的上游字段证据仍为 E1，不能升级到 E3。
- 2026-07-18 的一次受控资料请求在本地 `r.json()` 解析边界失败；调用次数为 `1`、自动
  重试为 `0`，未保存原始响应，也未创建 observed Fixture。该结果不能证明限流、拦截、
  接口失效、字段变化或客户端缺陷。
- 2026-07-25 的第二次独立受控资料观察在相同 `r.json()` 边界失败；逻辑调用和底层请求
  均为 `1`、自动重试和并发均为 `0`，未保存原始响应或完整 DataFrame，也未创建
  observed Fixture。它不是 2026-07-18 调用的重试，未来观察仍须重新单独授权。
- 2026-07-27 的第三次独立受控观察返回 HTTP `502`、Content-Type `text/html`，
  redirect `0`，随后在相同 `r.json()` 边界发生 `JSONDecodeError`；相同入口停止后续
  在线尝试。P1-04A 的 `cninfo` 建议仍为 Proposed，内部 Web API 自动化和脱敏 Fixture
  保存边界尚未获批准。
- synthetic Fixture 只验证离线调用链，不证明真实 `item/value` 响应结构或线上可用性。
- Eastmoney Profile Provider 不能被声明为在线可用，也不能进入正式 DataSource 路由。
- P2-04 仍未完成：Eastmoney News 当前只有 N2 静态证据和 synthetic Fixture，没有最小
  脱敏 observed Fixture；真实时间格式、字段类型、article code 稳定性、实际返回行为和
  在线可用性均未确认。
- Eastmoney News 当前只完成纯离线 Provider 和存储契约验证，不能被声明为在线可用，也
  不能进入正式 DataSource、Pipeline、Analyzer、Prompt、Report 或通知。
- 腾讯 QuoteProvider 的 B4 五日证据和合并候选门禁已经通过，但仍未获准进入正式或备用
  行情路由、分析、报告或通知。正式路由、降级、缓存、限流、重试和熔断仍须另立任务
  设计与验收。
