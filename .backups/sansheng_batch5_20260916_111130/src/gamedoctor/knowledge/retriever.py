"""知识检索（PRD §4.3 本地 RAG → 多源联网检索）。

错误签名匹配 + 可选向量检索（chromadb）。支持 SQLite 和向量检索两种模式。
"""

from __future__ import annotations

from typing import Protocol

from ..config import Config, load
from ..models import KnowledgeHit
from .store import SQLiteKnowledgeStore


class KnowledgeRetriever(Protocol):
    """知识检索协议。"""

    def search(self, signature: str) -> list[KnowledgeHit]:
        """按错误签名检索本地知识库。"""
        ...


class SQLiteRetriever:
    """SQLite 检索实现。"""

    def __init__(self, store: SQLiteKnowledgeStore | None = None):
        self.store = store or SQLiteKnowledgeStore()

    def search(self, signature: str) -> list[KnowledgeHit]:
        self.store.connect()
        try:
            rows = self.store.lookup(signature)
        finally:
            self.store.close()
        return [
            KnowledgeHit(key=r["signature"], score=1.0, repair_template=r["repair_template"])
            for r in rows
        ]


class VectorRetriever:
    """向量检索实现（基于 ChromaDB）。"""

    def __init__(self, config: Config | None = None):
        self.config = config or load()
        self._rag_module = None  # 延迟加载 rag_data 模块
        self._available = None  # 缓存是否可用的状态

    def _check_availability(self) -> bool:
        """检查向量检索是否可用。"""
        if self._available is not None:
            return self._available

        try:
            from ...rag import rag_data
            self._rag_module = rag_data
            self._available = True
            return True
        except ImportError:
            self._available = False
            return False

    @property
    def _rag_data(self):
        """延迟加载 rag_data 模块，避免在没有 ChromaDB 时导入失败。"""
        if not self._check_availability():
            raise RuntimeError("向量检索需要 ChromaDB，请安装：pip install 'gamedoctor[vector]'")
        return self._rag_module

    def search(self, signature: str, query: str = "") -> list[KnowledgeHit]:
        """使用向量检索查找相关游戏知识。"""
        if not self._check_availability():
            return []

        try:
            # 获取 RAG 数据目录
            rag_data_dir = self.config.get("knowledge.rag_data_dir")
            if not rag_data_dir:
                # 从 rag_data.py 获取默认数据目录
                from pathlib import Path
                rag_data_dir = str(Path(__file__).parent.parent.parent.parent / "rag")

            kb = self._rag_data.RAGKnowledgeBase(data_root=rag_data_dir)

            # 尝试从签名中提取游戏名
            game_name = self._extract_game_name(signature)

            # 如果有查询字符串，使用它；否则使用签名
            search_query = query or signature

            # 执行向量检索
            chunks = kb.search(game_name, search_query, k=5)

            # 转换为 KnowledgeHit 格式
            return [
                KnowledgeHit(
                    key=f"{chunk.game}:{chunk.source}:{hash(chunk.content)}",
                    score=max(0.0, 1.0 - chunk.score / 2.0),  # 将相似度分数转换为权重
                    repair_template=chunk.content
                )
                for chunk in chunks
            ]
        except Exception:
            # 向量检索失败时返回空列表，由上层处理回退
            return []

    def _extract_game_name(self, text: str) -> str:
        """从文本中尝试提取游戏名。这是一个简单的实现，可以根据需要优化。"""
        # 这里可以加入更复杂的游戏名识别逻辑
        # 目前先返回空字符串，让 RAG 模块自动查找
        return ""


class HybridRetriever:
    """混合检索器：结合 SQLite 和向量检索。"""

    def __init__(self, config: Config | None = None):
        self.config = config or load()
        self.sqlite_retriever = SQLiteRetriever()
        self.vector_retriever = VectorRetriever(config)
        self.auto_index = self.config.get("knowledge.auto_index", True)

    def search(self, signature: str, query: str = "") -> list[KnowledgeHit]:
        """组合 SQLite 和向量检索结果。"""
        results = []

        # SQLite 检索
        try:
            sqlite_results = self.sqlite_retriever.search(signature)
            results.extend(sqlite_results)
        except Exception:
            pass  # SQLite 检索失败时继续执行其他检索

        # 根据配置决定是否使用向量检索
        if self.config.get("knowledge.use_vector", False):
            try:
                vector_results = self.vector_retriever.search(signature, query)
                results.extend(vector_results)
            except Exception:
                # 向量检索失败时，如果启用了自动索引，尝试自动索引
                if self.auto_index and query:
                    try:
                        self._auto_index_games()
                        # 重试向量检索
                        vector_results = self.vector_retriever.search(signature, query)
                        results.extend(vector_results)
                    except Exception:
                        pass  # 自动索引失败，继续使用 SQLite 结果

        # 去重：根据 key 去除重复项，优先保留 SQLite 结果
        seen = set()
        unique_results = []
        for result in results:
            if result.key not in seen:
                seen.add(result.key)
                unique_results.append(result)
        results = unique_results

        # 按分数排序
        results.sort(key=lambda x: x.score, reverse=True)

        return results

    def _auto_index_games(self):
        """自动索引未索引的游戏。"""
        if not self.auto_index:
            return

        try:
            # 延迟导入，避免在没有 ChromaDB 时导入失败
            from ...rag import rag_data
            kb = rag_data.RAGKnowledgeBase(data_root=self.config.get("knowledge.rag_data_dir"))

            # 检查并索引所有游戏
            result = kb.index_all()

            # 只对有索引结果的进行日志输出
            indexed_games = {game: count for game, count in result.items() if count > 0}
            if indexed_games:
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"自动索引了 {len(indexed_games)} 个游戏: {indexed_games}")
        except Exception:
            # 自动索引失败时不阻断流程
            pass


def search_knowledge(signature: str) -> list[KnowledgeHit]:
    """模块级快捷入口：使用默认检索器按签名查询知识库。"""
    cfg = load()

    if cfg.get("knowledge.use_vector", False):
        retriever = HybridRetriever(cfg)
    else:
        retriever = SQLiteRetriever()

    return retriever.search(signature)


def search_knowledge_with_rag(signature: str, game_name: str, query: str = "") -> list[KnowledgeHit]:
    """专门用于 RAG 检索的快捷入口。"""
    cfg = load()
    retriever = HybridRetriever(cfg)
    return retriever.search(signature, query)


def check_and_index_games(game_names: list[str] = None) -> dict[str, int]:
    """检查并索引指定的游戏。如果未指定游戏，索引所有游戏。

    返回: {游戏名: 索引的文档数}
    """
    cfg = load()
    if not cfg.get("knowledge.use_vector", False):
        return {}

    try:
        from ...rag import rag_data
        kb = rag_data.RAGKnowledgeBase(data_root=cfg.get("knowledge.rag_data_dir"))

        if game_names:
            result = {game: kb.index(game) for game in game_names}
        else:
            result = kb.index_all()

        # 过滤掉索引为 0 的游戏
        return {k: v for k, v in result.items() if v > 0}
    except Exception:
        return {}


def get_available_games() -> list[str]:
    """获取 RAG 数据中可用的游戏列表。"""
    try:
        cfg = load()
        from ...rag import rag_data
        kb = rag_data.RAGKnowledgeBase(data_root=cfg.get("knowledge.rag_data_dir"))

        # 获取数据根目录下的所有游戏目录
        data_root = cfg.get("knowledge.rag_data_dir") or ""
        if data_root:
            from pathlib import Path
            games_dir = Path(data_root)
            if games_dir.exists():
                return [d.name for d in games_dir.iterdir()
                       if d.is_dir() and not d.name.startswith(".")]
        return []
    except Exception:
        return []
