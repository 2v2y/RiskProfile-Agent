"""LLM 接口封装层。

目标分层：Agent -> LLMClient -> LangChain -> Qwen 服务器。
Agent 不直接 import 模型调用代码，只依赖 LLMClient.generate()。
"""

from src.llm.client import DummyLLMClient, QwenClient, get_llm_client

__all__ = ["DummyLLMClient", "QwenClient", "get_llm_client"]
