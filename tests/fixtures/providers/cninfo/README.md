# CNInfo Profile synthetic Fixture

`profile_synthetic_minimal.json` 是为纯离线契约测试手工构造的单行宽表样本。它不是
observed Fixture，不是任何真实响应的副本，也不包含真实公司资料、URL、Header、
Cookie、Token、请求参数或响应元数据。

字段 `A股代码`、`A股简称`、`所属市场`、`所属行业` 只来自 P1-04A 的静态候选列清单；
值 `000000` 和所有 `SYNTHETIC_*` 文本都是测试占位值。此样本不证明线上响应结构、
字段类型、空值形态、接口稳定性、自动化访问许可或 Fixture 保存许可，不把任何字段
提升到 E3，也不能用于声明 CNInfo 在线可用。

Fixture 只能由测试或纯离线验收代码加载。正式 Parser 和 Provider 不读取该文件。
