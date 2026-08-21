"""LLM 客户端：统一接口，支持本地离线占位与服务器 Qwen。

用法：
    client = get_llm_client(config)     # 依据 configs/config.json 的 llm.provider 选择
    text = client.generate([{"role":"user","content":"..."}])

阶段1 默认 provider=dummy，不发网络请求；服务器部署时把 provider 改为 qwen。
"""

from __future__ import annotations

import os
from typing import Any, Protocol


class LLMClient(Protocol):
    model: str

    def generate(self, messages: list[dict[str, str]]) -> str:
        """输入 messages（OpenAI chat 格式），返回文本。"""
        ...


class DummyLLMClient:
    """阶段1离线占位实现：不发任何网络请求，供测试与联调用。"""

    def __init__(self, model: str = "dummy-stage1"):
        self.model = model

    def generate(self, messages: list[dict[str, str]]) -> str:
        return "STAGE1_PLACEHOLDER：阶段8接入Qwen后生成真实内容"


class QwenClient:
    """通过 LangChain 的 ChatOpenAI 调用服务器上的 Qwen（OpenAI 兼容接口）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 120.0,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def generate(self, messages: list[dict[str, str]]) -> str:
        from langchain_openai import ChatOpenAI  # 懒加载：不装 langchain 时不影响其他模块

        llm = ChatOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )
        response = llm.invoke(messages)
        return response.content


def _env_or(name: str, default: Any) -> Any:
    value = os.getenv(name)
    return value if value is not None else default


def get_llm_client(config: dict[str, Any]) -> LLMClient:
    llm_cfg = config.get("llm", {})
    provider = llm_cfg.get("provider", "dummy")
    if provider == "qwen":
        return QwenClient(
            base_url=str(_env_or("QWEN_BASE_URL", llm_cfg.get("base_url", ""))),
            api_key=str(_env_or("QWEN_API_KEY", llm_cfg.get("api_key", ""))),
            model=str(_env_or("QWEN_MODEL", llm_cfg.get("model", ""))),
            temperature=float(_env_or("QWEN_TEMPERATURE", llm_cfg.get("temperature", 0.0))),
            max_tokens=int(_env_or("QWEN_MAX_TOKENS", llm_cfg.get("max_tokens", 2048))),
            timeout=float(_env_or("QWEN_TIMEOUT", llm_cfg.get("timeout", 120.0))),
        )
    return DummyLLMClient(model=llm_cfg.get("model") or "dummy-stage1")
