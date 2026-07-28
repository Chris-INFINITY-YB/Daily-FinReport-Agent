# CN Profile 替代来源与 Transport 静态可行性评估

状态：**Accepted — `cninfo` 架构已批准，P1-04B 离线骨架已完成**

评估日期：2026-07-27；决策落实日期：2026-07-28
评估基线：`origin/main` `519fb61df91c5969164bacb8f42ddbb57c3d1642`

## 1. 背景与决策问题

现有 Eastmoney CN Profile 离线骨架以稳定 Provider ID `eastmoney` 表示原始来源，
AkShare 只是候选调用库。P1-04R、P1-04R2、P1-04R3 三次分别授权的受控观察均未形成
可供 Parser 使用的真实行记录，因此没有 observed Fixture，`name`、`industry` 仍为 E1。

本 ADR 的静态评估回答下一步应为 Eastmoney 建立有独立证据的新响应契约、建立新的
原始来源 Provider，还是暂停 P1-04。用户随后批准 `cninfo` 架构，P1-04B 已按本 ADR
建立纯离线 Provider 契约；仍没有调用候选数据 API、在线 Transport、生产路由或字段
证据升级。

## 2. 已知事实与不能据此推出的结论

三次观察的共同事实如下：

- 每次都是独立授权、独立计数的一个逻辑调用和一个实际 HTTP 请求；
- 自动重试均为 0，未形成 JSON 或 `item/value` 行记录，未创建 observed Fixture；
- P1-04R3 的安全诊断为 HTTP `502`、Content-Type `text/html`、redirect `0`，
  随后在本地 `r.json()` 边界发生 `JSONDecodeError`；
- 相同 `stock_individual_info_em` 入口已经停止后续在线尝试。

这些事实不能单独证明 WAF、反爬、永久接口失效、AkShare 缺陷或字段变化。它们只证明
当前入口未提供足以完成 P1-04 的响应证据。

以下做法不再作为候选方案：

- 对原入口做第四次相同请求；
- 仅更换 `requests` 参数、Header、User-Agent、Cookie、代理或重试策略；
- 把 synthetic Fixture 改名为 observed；
- 在缺少原始发布身份、响应契约和许可证据时保留 `eastmoney` Provider ID；
- 依据证券代码前缀静默猜测交易所。

## 3. 静态审计范围与证据等级

本机安装元数据静态确认 AkShare 版本仍为 `1.18.46`。审计只读取源码和包元数据，没有
导入或执行候选函数。相对路径、函数和完整文件 SHA-256 如下：

| 相对路径 | 函数 | 静态请求边界 | SHA-256 |
|---|---|---|---|
| `akshare/stock/stock_info_em.py` | `stock_individual_info_em`（13–69） | 一个 GET、一次 `r.json()`、无循环 | `3264436193901655cccf914560327ceb7fc7dbf919785984da6451ca5ea5d33f` |
| `akshare/stock/stock_profile_cninfo.py` | `stock_profile_cninfo`（30–111） | 一个 POST、一次 `r.json()`；一个结果整形循环 | `520507dc62b9d97976e6cdd62179459954e52b30dde901a24d38e3d25f862ac9` |
| `akshare/stock/stock_industry_cninfo.py` | `stock_industry_category_cninfo`（32–102）；`stock_industry_change_cninfo`（105–171） | 分类目录为一个 GET；变更记录为一个 POST | `faa80a58409c91db04377a39079edabc163b42b21668954146c384d7e8044e16` |
| `akshare/stock/stock_info.py` | `stock_info_sz_name_code`（20–118）；`stock_info_sh_name_code`（122–181）；`stock_info_bj_name_code`（185–283）；`stock_info_a_code_name`（440–468） | 深市一个 GET/Excel；沪市一个 GET/JSON；北市两个 POST 且分页循环；汇总函数自身无请求 | `d3288903d1e8ce7796e051bfbe651cb15aad0b29d4043277b04984f3cec72892` |
| `akshare/stock_fundamental/stock_basic_info_xq.py` | `stock_individual_basic_info_xq`（15–43） | 一个 GET、一次 `r.json()`，函数要求 `token` | `7e247063276e6b3a71766ea05c6c83b4fd660c6a5e49830a89cc194584c3ef74` |

静态源码能证明函数、字段标签和控制流候选，不能证明当前线上响应、真实类型、空值形态、
分类口径、访问稳定性或自动化许可。官方网页能证明发布身份、公开页面和明示条款，不能
自动证明内部接口适合无人值守调用。两类证据都不能替代 observed Fixture。

## 4. 候选证据

所有外部页面的访问日期均为 **2026-07-27**。本节把“事实”和“推断”分开记录。

### 4.1 A：Eastmoney 同源替代边界

事实：

- 现有静态源码只确认 `stock_individual_info_em` 使用 Eastmoney 原始主机；其请求和
  JSON 边界就是三次失败所经过的边界。
- 对本机 AkShare `1.18.46` 的 CN 股票资料模块做函数级静态检索，没有找到另一个同时
  提供单证券身份、简称和行业的独立 Eastmoney 公司资料入口。其他 Eastmoney 函数属于
  行情、排行、研报或专项数据，不能冒充稳定 Profile。
- [东方财富法律声明](https://about.eastmoney.com/home/disclaimer)不保证内容或服务的
  连续性、及时性和可靠性，并限制未经许可的复制、传播和可能影响系统运行的使用。

推断：

- Eastmoney 网页上存在公司资料展示，不等于存在可公开自动化、响应契约稳定且与失败
  边界独立的接口。
- 未识别出独立边界前，不能把客户端参数调整称为“新 Transport”，也不能证明它会避开
  相同网关风险。

结论：保留现有离线 `eastmoney` Provider/Parser 作为历史候选，但冻结在线方向；若未来
取得官方接口文档和独立响应证据，应建立版本化的新响应契约，不能让旧 Parser 静默接受
不同结构。

### 4.2 B：交易所官方来源

事实：

- [上交所股票列表](https://star.sse.com.cn/assortment/stock/list/share/)公开股票代码、
  证券简称和上市日期。当前 AkShare 静态列表函数也有代码、简称、全称和上市日期，但
  没有单证券行业字段。
- [上交所法律声明](https://www.sse.com.cn/home/legal/)允许在遵守声明前提下基于
  非商业目的浏览和下载，禁止未经书面许可的出售牟利式使用，并对准确性、完整性和
  及时性不作保证。
- [深交所股票页面](https://www.szse.cn/market/stock/company/)公开“股票列表”等入口。
  AkShare 静态列表整形字段包含 A 股代码、A 股简称、板块和`所属行业`。
- [深交所法律声明](https://www.szse.cn/application/laws/index.html)同样允许合规的
  非商业浏览、下载，限制未经书面许可的出售牟利式使用。
- [北交所股票列表](https://www.bse.cn/nq/listedcompany.html)提供按公司简称、拼音或
  代码检索的股票列表入口；AkShare 静态列表字段包含证券代码、证券简称、上市日期、
  地区和`所属行业`，但实现含两次 POST 和分页循环。
- [北交所新旧代码对照表](https://www.bse.cn/service/code_mapping.html)与
  [存量代码切换通知](https://www.bse.cn/important_news/200025603.html)直接证明存量
  股票存在新旧代码切换，单靠历史代码前缀不是稳定的交易所身份规则。
- 当前正式配置的两个 CN watchlist 项只有 `market`、`symbol` 和展示用 `name`，没有
  `exchange`；`Security.exchange` 虽然存在，但只是未枚举、未附来源证据的可选字符串。
  当前 watchlist 没有经过可审计身份层确认的北交所证券，但 `market="cn"` 的模型范围
  也没有排除北交所。

推断：

- 三家交易所的原始发布身份最强，但公开列表的字段和响应形态不对称。一个统一的
  Profile 能力至少需要三个 Provider ID（建议 `sse`、`szse`、`bse`）或一个有明确
  子来源归属的编排层。
- 当前 `Security.exchange` 是可选字符串，模型没有来源证明、枚举或新旧代码映射；
  因此不能安全地仅按 `symbol` 选择交易所。必须先建立独立的 exchange identity
  resolver、证据来源和冲突测试。
- 深市和北市静态列表含行业候选，不代表两者采用相同分类体系；沪市列表缺行业又使
  全市场字段完整度不一致。

结论：交易所列表适合作为将来的证券身份/交易所解析证据，也可能成为分市场 Profile
来源，但不适合在当前 P1-04B 直接作为统一首选。

### 4.3 C：CNInfo 官方披露与基础资料平台

事实：

- [巨潮资讯网首页](https://www.cninfo.com.cn/new/index.jsp)声明其为深圳证券交易所
  法定信息披露平台，由深交所全资子公司深圳证券信息有限公司运营，并链接上交所、
  深交所、北交所和 CNINFO Data Service。
- 巨潮首页公开“个股 F10”入口，并把数据平台、数据 API 等列为独立数据服务；首页
  免责声明不保证证券市场信息的准确性和完整性。当前公开证据没有给出内部 Web API
  自动化或 Fixture 再分发授权。
- AkShare `stock_profile_cninfo` 静态源码的原始主机属于 `cninfo.com.cn`，单证券参数
  被整形成零行或一行的宽表结果，而不是 Eastmoney 的 `item/value` 行。列标签候选包括
  `A股代码`、`A股简称`、`公司名称`、
  `所属市场`、`所属行业`、`主营业务`、`机构简介`和`经营范围`。
- 同一安装包中的 CNInfo 行业模块另有分类目录和行业变更记录，分类目录标签包括巨潮、
  证监会和申万等体系；Profile 中`所属行业`究竟对应哪一体系，静态源码没有建立关联。
- 该实现调用未公开承诺稳定性的 Web API 边界，并依赖运行时生成的访问校验值；函数
  没有显式 timeout 参数。
- 当前证据把深圳证券信息有限公司确认成平台运营方和数据访问来源，并把数据形态确认
  为公司概况；它没有逐字段确认是发行人直接资料、披露元数据还是平台汇编结果。

推断：

- CNInfo 是不同于 Eastmoney 的原始来源，若采用必须使用新 Provider ID `cninfo`；
  AkShare 与原始来源身份必须继续分离。
- 跨沪、深、北的单一公司资料形态、证券代码、简称、市场和行业候选，使它比三交易所
  分片方案更适合作为下一步离线契约对象。
- 公开网页可浏览不等于对内部 Web API 的长期无人值守访问、仓库 Fixture 再分发或
  生产使用许可。自动化稳定性和 Fixture 保存权仍是上线前门禁。

结论：作为新的 `cninfo` Profile Provider 候选进入 P1-04B 离线契约设计；当前不批准
在线 Transport，不声明在线可用。

### 4.4 D：需要账号、密钥或商业条款的来源

#### Tushare Pro

事实：

- [股票基础信息文档](https://tushare.pro/document/1?doc_id=25)的 `stock_basic` 包含
  `symbol`、`name`、`industry`、`market`、`exchange` 和 `curr_type`，覆盖全市场 A 股；
  文档标明每次最多 6000 行、2000 积分起和每分钟 50 次。
- [上市公司基本信息文档](https://tushare.pro/document/2?doc_id=112)的
  `stock_company` 包含公司全称、交易所、公司介绍、主营业务和经营范围，要求至少
  120 积分。
- 当前文档没有提供不注册、不使用 Token 的匿名免费额度；积分可能通过平台规则取得，
  但对 CI 和普通贡献者仍是账号与权限门禁。
- [用户协议](https://tushare.pro/document/1?doc_id=409)要求注册登录；
  [数据服务协议](https://tushare.pro/document/1?doc_id=405)要求保管账号、密码和
  Token，并将服务许可描述为个人、不可转让、非商业、可撤销、有期限且非排他。

推断与结论：

- 字段覆盖良好且官方文档清晰，但 CI/贡献者不能在无账号和 Token 时复现在线边界；
  observed Fixture 入库和后续使用还需单独许可确认。它不应成为当前默认首选，状态为
  “暂停，等待商业与再分发批准”，建议 Provider ID `tushare`。

#### Xueqiu

事实：

- AkShare 静态函数 `stock_individual_basic_info_xq` 的签名要求 `token`，并向
  `xueqiu.com` 原始主机发起请求。
- [雪球服务协议](https://xueqiu.com/about/terms)禁止未经授权使用爬虫、抓取工具或
  非人工方式访问、存储、缓存或索引服务内容，并限制获取原始数据和绕过保护机制。

推断与结论：

- 该候选同时存在凭据、自动化许可和 Fixture 再分发风险，即使字段形态可用也不能作为
  本项目的默认 Profile 来源。状态为“废弃候选”，建议 Provider ID `xueqiu` 仅用于
  记录来源身份，不进入实现。

## 5. 比较矩阵

### 5.1 身份、覆盖与字段

| 候选 | 原始发布方 / Provider ID | 调用库与来源 | 覆盖 | `name` 候选 | `industry` 候选 / 体系 | `exchange` / `currency` / `description` | 身份交叉校验 |
|---|---|---|---|---|---|---|---|
| Eastmoney 未识别的新边界 | 东方财富 / `eastmoney` | 必须分离；当前仅有 AkShare 旧边界 | CN，实际新边界未证实 | 旧边界候选`股票简称`，新边界未知 | 旧边界候选`行业`，体系未知 | 均无新证据 | 旧边界有代码候选；新边界未知 |
| 上交所列表 | 上海证券交易所 / `sse` | 可直接来源或独立客户端 | 上交所证券 | 证券简称，官方列表与静态源码 | 当前列表无单证券行业证据 | 上市地点可由来源固定；货币、简介无证据 | 代码、简称、上市日期 |
| 深交所列表 | 深圳证券交易所 / `szse` | 可直接来源或独立客户端 | 深交所 A 股 | A 股简称，静态源码 | `所属行业`；分类体系未由当前证据固定 | 上市地点可由来源固定；货币、简介无证据 | A 股代码、简称、板块 |
| 北交所列表 | 北京证券交易所 / `bse` | 可直接来源或独立客户端 | 北交所股票 | 证券简称，官方入口与静态源码 | `所属行业`；分类体系未由当前证据固定 | 上市地点可由来源固定；货币、简介无证据 | 代码、简称、上市日期、新旧代码表 |
| CNInfo 公司资料 | 深圳证券信息有限公司 / `cninfo` | AkShare 只是候选客户端，必须与来源分离 | 静态与公开页面显示沪、深、北公司资料 | `A股简称`；另有`公司名称` | `所属行业`；具体分类体系未确认 | `所属市场`候选；货币无证据；`机构简介`等为 description 候选 | `A股代码`、简称、所属市场 |
| Tushare Pro | 北京沃远数据科技有限公司 / `tushare` | 官方 SDK/API 与来源同一服务身份 | 全市场 A 股 | `name` | `industry`；文档未在该页固定分类标准 | `exchange`、`curr_type`、`introduction`均有文档字段 | TS 代码、symbol、exchange |
| Xueqiu | 雪球 / `xueqiu` | AkShare 是第三方客户端 | 静态函数为单证券，完整覆盖未核实 | 静态字段候选，不采纳 | 静态字段候选，体系未核实 | 无可采纳证据 | symbol 候选，但凭据边界不合格 |

### 5.2 响应、许可、运维与关系决策

| 候选 | 响应与静态审计 | 认证/付费 | 条款与 Fixture 风险 | 稳定性/维护风险 | 新依赖 | 离线保证 | 最小 observed 方案 | 与 Eastmoney 关系 / 状态 |
|---|---|---|---|---|---|---|---|---|
| Eastmoney 未识别的新边界 | 没有独立响应契约 | 未确认 | 法律声明限制复制传播；Fixture 权利未确认 | 可能共享网关；无独立证据 | 未定 | 可保持，但没有可测试契约 | 先取得官方契约与许可，再单次观察 | 保留旧骨架、冻结在线；暂停 |
| 上交所列表 | JSON 列表静态可审计 | 公开页面无登录 | 非商业浏览下载可行；再分发仍需审查 | 列表较稳，但缺行业 | 不需要 | 可用 synthetic/fixture transport | 单次列表观察只保留代码、简称、类型 | 新 `sse` Provider；身份层候选 |
| 深交所列表 | Excel 列表静态可审计 | 公开页面无登录 | 非商业浏览下载可行；再分发仍需审查 | 下载格式和行业口径可能变化 | Excel 解析已有间接能力但不应提前新增 | 可保持 | 单次下载，只保留字段名/类型/脱敏行 | 新 `szse` Provider；分市场候选 |
| 北交所列表 | POST + 分页，静态复杂度最高 | 公开页面无登录 | 明确 Fixture 再分发许可未找到 | 分页、两请求和代码切换风险高 | 不需要 | 可保持 | 需先固定新旧代码和分页上限，再受控观察 | 新 `bse` Provider；身份层候选 |
| CNInfo 公司资料 | 单证券 JSON 后整形成零/一行宽表，静态可审计 | 公开页无登录；Web API 许可/付费状态未确认 | 公开浏览不构成内部 API 或 Fixture 再分发授权，两者均待确认 | 内部边界无稳定承诺、访问校验、无显式 timeout | P1-04B 未新增；未来直接 Transport 另审 | 导入、构造、测试、dry-run 均可纯离线 | 获批后单证券、单请求、无重试；只留代码/简称/市场/行业的字段名、类型和脱敏值 | 新 `cninfo` Provider 与 Eastmoney 并存；**离线架构已接受并落实** |
| Tushare Pro | 文档化 JSON/API | 注册、Token、积分/可能付费 | 个人不可转让非商业许可；仓库 Fixture 风险高 | 配额、权限和服务期变化 | 需要 Tushare SDK 或直接客户端 | 单测可离线，贡献者不能在线复现 | 只有取得账号与再分发批准后才可设计 | 新 `tushare` Provider；暂停 |
| Xueqiu | 单证券 JSON 静态候选 | Token/登录态 | 协议明确限制爬虫和原始数据获取 | 凭据和访问保护变化风险高 | 不应新增 | 单测可离线但无法合法建立证据 | 不设计 | 废弃候选 |

共同的安全日志要求：只记录稳定 Provider ID、operation、封闭终态、耗时、请求/返回/
issue 计数和安全错误码；不得记录证券、URL、查询参数、Header、Cookie、Token、响应正文、
异常正文或动态资料值。任何候选失败必须隔离在 Provider 边界，不能触发其他来源的隐式
请求或降级。

## 6. 决策

**结论：推荐建立新的原始来源 Provider，与 Eastmoney 并存或替代。**

CNInfo 已被接受为稳定 ID 为 `cninfo` 的独立 Profile Provider；P1-04B 仅落实离线契约。
接受理由是：

1. 原始发布身份与 Eastmoney 明确不同，不能也无需借用 `eastmoney` ID；
2. 单一公司资料形态静态覆盖简称、行业、市场和证券代码候选，避免当前就引入三交易所
   路由；
3. 可以延续“显式 Transport 注入 + 纯 Parser + 标准 `ProviderResult`”架构，并保持
   导入、构造、测试和 dry-run 完全离线；
4. 它的公开页面身份和字段候选足以支持下一步 synthetic 离线契约设计，但不足以批准
   在线 Transport，正好把静态可行性与在线可用性分开。

这不是对 CNInfo 内部 Web API、自动化访问、生产许可或 observed Fixture 再分发权的
批准。若后续许可或受控观察门禁无法满足，P1-04 应继续暂停，而不是悄悄回退到第四次
Eastmoney 请求。

## 7. 架构影响

### Provider ID 与来源身份

- 新 Provider ID：`cninfo`；`SecurityProfile.source` 只能写 `cninfo`。
- AkShare 若未来被选为临时调用库，也不能出现在 `source` 中。
- 现有 `eastmoney` 离线 Provider、Parser 和 synthetic Fixture保持原样并继续冻结在线
  使用；本 ADR 不废弃历史代码，也不允许把 CNInfo 行记录送进 Eastmoney Parser。

### 模型

- `SecurityProfile` 暂不改字段。
- `name` 候选为`A股简称`；`industry` 候选为`所属行业`；二者仍是静态字段候选，不能
  升级到 E3。
- `exchange` 可研究`所属市场`的封闭映射，但只有 observed 证据、身份冲突测试和明确
  词表后才能写入；`currency` 继续为 `None`。
- `description` 的`机构简介`、`主营业务`、`经营范围`候选不能拼接；需要字段级许可、
  长度和不可信文本策略后另行决定，P1-04B 默认仍为 `None`。
- 当前 `Security.exchange` 不足以支撑交易所路由；不在 P1-04B 中增加静默推断。

### Parser 与 Transport

- 新建独立 Parser/Transport Protocol；CNInfo 静态结果是零/一行宽表，与 Eastmoney
  `item/value` 契约不同，字段表、身份语义、空值和错误边界都必须版本化，不能复用
  Eastmoney 映射。
- P1-04B 只允许 Fixture Transport，不实现网络客户端，不导入 AkShare。
- 将来在线 Transport 必须另立任务，显式 timeout、单请求预算、无自动重试、无隐式
  分页/降级，并复用现有安全错误层和 P4-02 安全事件边界。

### Fixture

- P1-04B 只能创建明确标记的 synthetic Fixture。
- 将来的 observed Fixture 最少只保留实际字段名、基础类型、脱敏占位值及身份一致性
  证据；不得保留公司名称、行业真实值、简介正文、动态数据、URL、请求参数或凭据。
- observed Fixture 入库前必须确认自动化访问与最小脱敏再分发边界；没有确认就不观察。

## 8. P1-04B 落实状态

已完成任务：

> **P1-04B：CNInfo CN Profile 离线 Provider 契约与 synthetic Fixture**

明确范围：

- 新增稳定 Provider ID `cninfo`、Descriptor、只读 Transport Protocol、纯 Parser 和
  显式依赖注入 Provider；
- 只接受本地 synthetic 零/一行宽表记录，固定代码、简称、行业、市场候选及缺失/冲突
  语义；
- 复用标准 `SecurityProfile`、`ProviderResult`、Provider 错误和安全日志事件；
- 不实现在线 Transport，不导入/执行 AkShare，不修改正式 DataSource、配置、路由、
  Analyzer、Prompt、Report、通知、数据库或 Eastmoney 资产。

已实现文件：

- `daily_report_agent/providers/cninfo/__init__.py`
- `daily_report_agent/providers/cninfo/constants.py`
- `daily_report_agent/providers/cninfo/transport.py`
- `daily_report_agent/providers/cninfo/parser.py`
- `daily_report_agent/providers/cninfo/profile.py`
- `tests/unit/providers/cninfo/`
- `tests/fixtures/providers/cninfo/profile_synthetic_minimal.json`
- `tests/fixtures/providers/cninfo/README.md`
- `docs/cn_profile_provider_contract.md`
- `docs/parallel_development_plan.md`
- 必要时只更新 `pyproject.toml` 的显式包清单和公开安全导出测试。

离线测试覆盖正常、name/industry/market 分别或同时缺失、空结果、多行、非法 Mapping/
列类型、未知列、非字符串值、身份一致/缺失/冲突、aware/naive/非法 clock，以及
network、timeout、rate-limit、blocked 和通用不可用错误映射。宽表 Mapping 不虚构
重复 item 语义；所有 Fixture 与异常样本均明确为 synthetic。

P1-04B 只新增 Descriptor、只读 Protocol、纯 Parser、显式注入 Provider、synthetic
Fixture、测试和文档。它没有实现在线 Transport，没有进入正式 DataSource、配置、路由、
Analyzer、Prompt、Report、通知或数据库，也没有改变 Eastmoney 骨架。CNInfo 与
Eastmoney 当前没有主备、降级或正式优先级。

observed 门禁：

1. 用户批准 `cninfo` Provider ID 和本 ADR 的来源选择；
2. 单独确认内部边界的自动化访问及最小脱敏 Fixture 保存许可；
3. 另立 P1-04C 并取得一次单证券、单逻辑调用、最多一个 HTTP 请求、timeout 不超过
   10 秒、无重试/并发/分页/降级的明确在线授权；
4. 只在真实形成可验证行记录时创建 observed Fixture；失败立即停止；
5. `name`、`industry` 只有在 observed 契约、身份和空值测试全部通过后才可升级到 E3；
6. E3、双版本离线门禁、在线安全审查和用户路由批准完成前，禁止进入正式路由。

## 9. P1-04C 前仍需确认

用户已经批准 `cninfo` Provider ID、CNInfo 作为下一离线来源以及与 Eastmoney 并存。
任何 P1-04C 在线观察前仍必须：

1. 确认内部边界的自动化访问许可；
2. 确认最小脱敏 observed Fixture 的保存许可；
3. 另行取得单证券、单调用、单请求、无重试/并发/分页/降级的明确在线授权。

当前并未声明上述许可已经取得。P1-04 保持未完成，Eastmoney `name`、`industry` 保持
E1，CNInfo 候选字段只有静态/synthetic 证据，P2-04 和 P3 状态不变。

## 10. 证据索引

除第 3 节列出的本机 AkShare `1.18.46` 静态源码外，外部一手来源均于 2026-07-27
访问：

- [东方财富法律声明](https://about.eastmoney.com/home/disclaimer)
- [上交所股票列表](https://star.sse.com.cn/assortment/stock/list/share/)
- [上交所法律声明](https://www.sse.com.cn/home/legal/)
- [深交所股票页面](https://www.szse.cn/market/stock/company/)
- [深交所法律声明](https://www.szse.cn/application/laws/index.html)
- [北交所股票列表](https://www.bse.cn/nq/listedcompany.html)
- [北交所新旧代码对照表](https://www.bse.cn/service/code_mapping.html)
- [北交所存量代码切换通知](https://www.bse.cn/important_news/200025603.html)
- [巨潮资讯网](https://www.cninfo.com.cn/new/index.jsp)
- [Tushare 股票基础信息](https://tushare.pro/document/1?doc_id=25)
- [Tushare 上市公司基本信息](https://tushare.pro/document/2?doc_id=112)
- [Tushare 用户协议](https://tushare.pro/document/1?doc_id=409)
- [Tushare 数据服务协议](https://tushare.pro/document/1?doc_id=405)
- [雪球服务协议](https://xueqiu.com/about/terms)
