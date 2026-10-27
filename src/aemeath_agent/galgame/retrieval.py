"""心动模式 · 记忆检索（兼容层）。

检索实现已上移为通用模块 :mod:`aemeath_agent.retrieval`——代码检索与记忆检索
共用同一套可插拔后端，避免两份几乎相同的哈希/向量代码各自漂移。

这里只做转出，历史调用方（含既有单测）继续 ``from .retrieval import ...``。
"""

from __future__ import annotations

from ..retrieval import (
    DIM,
    EmbeddingRetriever,
    HashingRetriever,
    Retriever,
    build_retriever,
    embed,
    tokenize,
)

__all__ = [
    "DIM",
    "Retriever",
    "HashingRetriever",
    "EmbeddingRetriever",
    "build_retriever",
    "tokenize",
    "embed",
]
