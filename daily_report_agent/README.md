# 每日股市新闻分析报告智能体

每天定时抓取关注股票的新闻和行情，用 LLM 分析生成一份中文 Markdown 日报，并可推送到邮箱 / Telegram / 微信。

支持 **美股**（finnhub + yfinance）和 **A股**（akshare，免费免 token）。分析引擎默认 **DeepSeek**（OpenAI 兼容，可切换到 MiniMax / OpenAI）。

数据抓取与 prompt 结构借鉴了本仓库 `fingpt/FinGPT_Forecaster` 模块，但不涉及模型训练，纯云端 API，无需 GPU。

## 目录结构

```
Fin_Agent/
├── pyproject.toml             # 包定义、CLI 和 pytest 配置
├── tests/                     # 离线测试
└── daily_report_agent/
    ├── __init__.py
    ├── __main__.py            # python -m daily_report_agent 入口
    ├── config.yaml            # 关注列表、数据、LLM、推送渠道开关
    ├── .env.example           # API keys 样例(拷成 .env 填写)
    ├── datasource/            # 数据源: base(接口) / us(美股) / cn(A股)
    ├── llm_client.py          # OpenAI 兼容客户端
    ├── analyzer.py            # 组装 prompt + 调 LLM
    ├── report.py              # 渲染并存档 Markdown 报告
    ├── notifier.py            # 邮箱 / Telegram / Server酱推送
    ├── main.py                # 编排与 CLI
    ├── com.user.dailyreport.plist
    └── reports/               # 生成的报告存档(git 忽略)
```

## 环境与安装

支持 Python **3.10～3.13**。以下命令均从包含 `pyproject.toml` 的仓库根目录执行。

创建并激活虚拟环境（macOS / Linux）：

```bash
python -m venv .venv
source .venv/bin/activate
```

只运行不联网的 dry-run 和测试时，安装最小依赖（pip 安装过程本身可能需要联网下载依赖）：

```bash
python -m pip install -e ".[test]"
```

需要正式抓取、LLM 和通知功能时，安装现有在线功能依赖：

```bash
python -m pip install -e ".[online,test]"
```

## 启动

dry-run 使用确定性的本地占位数据，不读取 `.env`，不访问网络、不调用真实 LLM，也不会发送邮件、Telegram 或 Server 酱消息：

```bash
python -m daily_report_agent --dry-run
```

成功后会在 `daily_report_agent/reports/YYYY-MM-DD.md` 生成可检查的 Markdown 报告。

正式运行前配置密钥：

```bash
cp daily_report_agent/.env.example daily_report_agent/.env
```

正式跑一次但不推送：

```bash
python -m daily_report_agent --no-notify
```

完整运行（仅会执行 `config.yaml` 中启用的通知渠道）：

```bash
python -m daily_report_agent
```

安装后也可以使用等价 CLI：

```bash
daily-report-agent --dry-run
```

## 测试

测试全部使用临时配置和本地 Fixture，不依赖网络、API Key、用户 `.env` 或当前工作目录：

```bash
python -m pytest
```

## 常见启动错误

- `No module named daily_report_agent`：确认当前目录包含 `pyproject.toml`，或重新执行 `python -m pip install -e ".[test]"`。
- `No module named yaml`：当前虚拟环境尚未安装项目，请执行上述安装命令。
- `No module named yfinance`、`finnhub`、`openai`：正式运行需要 `python -m pip install -e ".[online]"`。
- 不要再使用 `python daily_report_agent/main.py` 或进入包目录执行 `python main.py`；标准入口是 `python -m daily_report_agent`。
- 如果启动的 Python 与安装依赖的 Python 不一致，使用 `python -m pip --version` 和 `python --version` 检查二者是否属于同一虚拟环境。

## 申请 key

| 用途 | 平台 | 说明 |
|------|------|------|
| LLM | [DeepSeek](https://platform.deepseek.com/) | 默认引擎，填入 `LLM_API_KEY` |
| 美股新闻 | [finnhub](https://finnhub.io/) | 免费额度够用，填入 `FINNHUB_KEY` |
| A股 | akshare | 无需 key |

切换 LLM：改 `daily_report_agent/config.yaml` 的 `llm.provider`（`deepseek`/`minimax`/`openai`），`LLM_API_KEY` 换成对应平台的 key 即可。

## 配置关注列表

编辑 `daily_report_agent/config.yaml` 的 `watchlist`，美股用 ticker（`AAPL`），A股用 6 位代码（`600519`）：

```yaml
watchlist:
  - {market: us, symbol: AAPL, name: 苹果}
  - {market: cn, symbol: "600519", name: 贵州茅台}
```

## 推送配置

在 `daily_report_agent/config.yaml` 的 `notify` 里把想用的渠道设为 `true`，再到 `daily_report_agent/.env` 填对应凭据：

- **邮箱**：最通用，用邮箱的「授权码」（非登录密码）。SMTP 465 端口走 SSL。
- **Telegram**：需 bot token 和 chat id。
- **微信**：用 [Server酱](https://sct.ftqq.com/)，填 `SERVERCHAN_SENDKEY`。

缺凭据的渠道会自动跳过并打印告警，不中断报告生成。

## 定时执行（macOS）

用 `daily_report_agent/com.user.dailyreport.plist`（launchd，比 cron 更适合 macOS，休眠唤醒后会补跑）。把文件里的 `/PATH/TO/...` 占位替换成真实路径后：

```bash
cp daily_report_agent/com.user.dailyreport.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.dailyreport.plist
launchctl start com.user.dailyreport   # 手动触发一次测试
```

**cron 备选**：`crontab -e` 加一行（每天 8:30）：

```
30 8 * * * cd /绝对路径/Fin_Agent && /绝对路径/python -m daily_report_agent >> daily_report_agent/reports/cron.log 2>&1
```

## 免责声明

本工具由 AI 自动生成分析，仅供学习研究，不构成投资建议。
