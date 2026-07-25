# Eastmoney Provider 合成 Fixture

`profile_synthetic_minimal.json` 是人工构造、确定性且始终离线的最小示例，只包含当前
Parser 候选 `股票简称` 和 `行业` 所需的两条 `item/value` 行。文件名和值均明确标记为
synthetic；它不包含真实公司资料、动态数值或复制的上游正文。

该文件不是 2026-07-18 失败请求的响应，也不是 observed、captured、real 或 production
Fixture。它不能证明 Eastmoney 真实响应具有 `item/value` 结构，不能验证真实字段类型、
空值或证券身份，也不能把 `name` 或 `industry` 的证据等级从 E1 提升到 E3。P1-04 仍须
由新的受控观察取得真实响应后，再制作最小化、脱敏的 observed Fixture。

本目录不保存失败响应、原始响应、真实上游完整 URL、Cookie、Token、Header 或请求日志。合成
Fixture 只用于验证离线验收入口能够经过 Fixture Transport、正式
`EastmoneyProfileProvider`、Parser 和标准模型完成一次可重复执行。

`news_synthetic_multiple.json` 是人工构造的公司新闻公开行集合，只用于 News Provider
的纯离线契约测试。它包含多条合法记录、缺摘要、缺 URL、仅日期、窗口外记录和相同发布
时间记录；证券、媒体、标题和摘要均明确标记为 synthetic，链接只使用
`example.invalid`。测试代码读取该文件后转换为不可变行，正式 Parser 不读取文件。

新闻 Fixture 不是 observed Fixture，也不是任何真实响应的复制。它不能证明 Eastmoney
真实时间格式、字段类型、空值、article code、链接或实际返回条数，不能把新闻证据从 N2
提升到 N3/N4，也不证明 AkShare 或 Eastmoney 在线可用。P2-04 总项仍等待受控真实响应
制作的最小脱敏 observed Fixture。
