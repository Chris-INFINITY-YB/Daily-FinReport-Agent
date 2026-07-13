"""
OpenAI 兼容的 LLM 客户端, 支持在 DeepSeek / MiniMax / OpenAI 间切换。

设计仿照项目里的 finogrid/fingpt_integration/minimax_llm_client.py:
用同一个 openai 库 + 自定义 base_url 接入多家兼容 OpenAI 协议的服务,
不引入额外依赖。
"""
from __future__ import annotations

import os

# 各 provider 的默认 base_url / model, 供 config 未显式指定时兜底
PROVIDER_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "minimax": {"base_url": "https://api.minimax.io/v1", "model": "MiniMax-M3"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}


class LLMClient:
    def __init__(
        self,
        provider: str = "deepseek",
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ):
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["deepseek"])
        self.base_url = base_url or preset["base_url"]
        self.model = model or preset["model"]
        self.temperature = temperature
        self.max_tokens = max_tokens

        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise ValueError(
                "缺少环境变量 LLM_API_KEY。请在 .env 中填写你的 LLM API key。"
            )
        from openai import OpenAI  # 延迟导入, dry-run 时无需安装 openai
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """发送 system + user 消息, 返回助手回复文本。失败时抛出异常由上层兜底。"""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return resp.choices[0].message.content.strip()
