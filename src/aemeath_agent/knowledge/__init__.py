"""③ 本地知识库（PRD §4.3 本地知识沉淀）。

成功修复后提取 ``(游戏指纹, 错误特征, 修复动作序列)`` 三元组存入 SQLite，
错误签名匹配 + 可选向量检索（chromadb）。
"""

# 对外统一导出知识库模块的存储协议、SQLite 实现与检索入口
from .retriever import KnowledgeRetriever, search_knowledge
from .store import KnowledgeStore, SQLiteKnowledgeStore

__all__ = [
    "KnowledgeStore",
    "SQLiteKnowledgeStore",
    "KnowledgeRetriever",
    "search_knowledge",
]
