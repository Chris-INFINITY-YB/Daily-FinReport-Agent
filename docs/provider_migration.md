# Provider 迁移边界（阶段 2-A）

## 当前生产结构

生产链路保持为：

```text
main.py
→ get_source()
→ CNDataSource / USDataSource
→ StockData
→ stockdata_to_analysis_input()
→ Analyzer
→ Report
```

`daily_report_agent/providers/` 在 2-A 只是旁路契约。`main.py` 没有导入或编排新
Provider，`LegacyDataSourceFacade` 也只供离线迁移测试使用。

## 当前能力拆分

### CNDataSource

现有 `daily_report_agent/datasource/cn.py` 是一个混合数据源：

- `CNDataSource.fetch()`（第 20～32 行）创建并组装 `StockData`，确定日期窗口，依次调度行情、简介和新闻，并聚合“无有效数据”错误。
- `_fetch_prices()`（第 34～48 行）调用 AkShare 历史行情，读取区间首尾收盘价并计算多日区间涨跌；异常文本写入旧 `StockData.error`。
- `_fetch_intro()`（第 50～62 行）读取个股资料，补证券名称并拼装旧 `intro` 文本。该文本当前还包含总市值等随时间变化的数据，不能直接视为新的 `SecurityProfile`。
- `_fetch_news()`（第 64～79 行）读取个股新闻、截断摘要并组装旧新闻对象，同时沿用旧错误聚合规则。
- 上述方法直接进行旧格式解析和字段标准化；真正到标准模型的转换仍由 `ingestion/adapters.py` 中的 `stockdata_to_collection()` 完成。

本阶段不移动、不改写这些职责，也未实例化任何真实 CN Provider Descriptor。

### USDataSource

现有 `daily_report_agent/datasource/us.py` 同样是混合数据源：

- `USDataSource.fetch()`（第 20～33 行）创建和组装 `StockData`，调度三类能力，并把未被内部处理的异常聚合进旧 `error` 字段。
- `_fetch_prices()`（第 35～46 行）使用 yfinance 历史行情，读取区间首尾收盘价并计算多日区间涨跌。
- `_fetch_intro()`（第 48～60 行）使用 Finnhub profile，补名称、行业、交易所、币种和市值，并拼成旧 `intro` 文本。
- `_fetch_news()`（第 62～76 行）使用 Finnhub company news，排序、过滤营销内容、限制数量并组装旧新闻对象。
- 日期、数值、新闻文本与错误仍按旧链路规则处理，标准模型转换继续由既有 adapter 负责。

本阶段不移动、不改写这些职责，也未实例化 yfinance 或 Finnhub 的新 Provider Descriptor。

## 阶段 2-A 的边界

已建立同步的 `QuoteProvider`、`NewsProvider`、`ProfileProvider` Protocol，稳定的
`ProviderDescriptor`/`ProviderResult` 契约，安全 Provider 错误层和独立
`SecurityProfile` 模型。旁路 `LegacyDataSourceFacade` 的方向固定为：

```text
旧 DataSource → StockData → stockdata_to_collection() → CollectedSecurityData
```

以下内容仍被冻结：

- 真实 Provider 尚未实现；
- Legacy Façade 尚未进入生产；
- `provider_calls` 不产生记录；
- `config.yaml` 尚未增加 Provider 配置；
- `main.py` 与生产编排未改变；
- SQLite schema 未改变；
- 不存在从新 Provider 反向转换为 `StockData` 的适配器。

Legacy Descriptor 使用 `legacy-cn-datasource` 或 `legacy-us-datasource`，明确表示整条
混合旧链路，不能冒充 AkShare、yfinance 或 Finnhub 的精确调用来源。

## 金融语义冻结

```text
MarketSnapshot ≠ PriceWindow
```

`QuoteProvider` 只返回某个观察时刻的单点 `MarketSnapshot`。当前旧 DataSource 则读取
一个日期区间的首尾价格并计算区间涨跌，适配后必须继续位于
`CollectedSecurityData.price_window`。Legacy Façade 不生成快照，输出的
`market_snapshots` 保持空元组。

因此，未来腾讯实时行情的单点价格和当日涨跌不能直接替换当前报告中的多日区间涨跌，
也不能把 `MarketSnapshot.pct_change` 填入 `PriceWindow.period_pct_change`。历史行情能力
需要在后续阶段另行设计，不能塞入当前 Quote Protocol。

## 后续迁移顺序

1. 2-B1：腾讯 `QuoteProvider` 离线实现与契约测试。
2. 2-B2：腾讯 `QuoteProvider` 受控在线冒烟。
3. 2-C：Profile Provider 迁移。
4. 2-D：News Provider 迁移。
5. 2-E：Provider 编排、降级和 `provider_calls` 接入。

在 2-E 之前不允许伪造或写入 `provider_calls`。
