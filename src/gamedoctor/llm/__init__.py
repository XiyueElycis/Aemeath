"""⑤ LLM 接入与模型路由（PRD §4.6 F6）。

- :mod:`client` —— httpx 封装（deepseek / Qwen / Ollama）
- :mod:`router` —— 多模型路由 + 交叉审查；离线用 Ollama 兜底
"""

# 统一导出 LLM 客户端（httpx 封装）与多模型路由
from .client import LLMClient, HttpLLMClient
from .router import ModelRouter

__all__ = ["LLMClient", "HttpLLMClient", "ModelRouter"]
