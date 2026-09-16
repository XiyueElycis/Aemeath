"""知识检索（PRD §4.3 本地 RAG → 多源联网检索）。

错误签名匹配 + 可选向量检索（chromadb）。支持 SQLite 和向量检索两种模式。
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Protocol

from ..config import Config, load
from ..models import KnowledgeHit
from .seed import DEFAULT_RAG_ROOT, seed_from_rag
from .store import SQLiteKnowledgeStore

logger = logging.getLogger(__name__)

# 顶层 rag/rag_data.py 与 src/ 平级，不在 gamedoctor 包内，无法用相对导入
# （旧代码 ``from ...rag import rag_data`` 必然 ImportError）；按文件路径加载。
_RAG_DATA_PY = Path(__file__).resolve().parents[2] / "rag" / "rag_data.py"
_rag_data_cache = None  # 三态：None=未尝试 / False=不可用 / module=已加载


class KnowledgeRetriever(Protocol):
    """知识检索协议。"""

    def search(self, signature: str) -> list[KnowledgeHit]:
        """按错误签名检索本地知识库。"""
        ...


class SQLiteRetriever:
    """SQLite 检索实现。首次检索遇到空库时自动从 rag/ 灌库一次。"""

    def __init__(self, store: SQLiteKnowledgeStore | None = None,
                 auto_seed: bool = True):
        self.store = store or SQLiteKnowledgeStore()
        self.auto_seed = auto_seed
        self._seed_attempted = False

    def search(self, signature: str) -> list[KnowledgeHit]:
        self.store.connect()
        try:
            if self.auto_seed and not self._seed_attempted:
                self._seed_attempted = True
                if self.store.count_entries() == 0:
                    try:
                        seed_from_rag(store=self.store)
                    except Exception:
                        # 灌库失败不阻断检索：退化为空结果，由上层兜底
                        logger.warning("SQLite 知识库自动灌库失败", exc_info=True)
            rows = self.store.lookup(signature)
        finally:
            self.store.close()
        return [
            KnowledgeHit(key=r["signature"], score=1.0, repair_template=r["repair_template"])
            for r in rows
        ]


def _load_rag_data_module():
    """按路径加载顶层 ``rag/rag_data.py``；缺少 langchain/chromadb 等依赖时返回 None。"""
    global _rag_data_cache
    if _rag_data_cache is False:
        return None
    if _rag_data_cache is not None:
        return _rag_data_cache
    try:
        spec = importlib.util.spec_from_file_location("gamedoctor_rag_data", _RAG_DATA_PY)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {_RAG_DATA_PY} 创建模块 spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _rag_data_cache = module
    except Exception:
        # rag_data 依赖 langchain/chromadb（vector extra），未安装时属预期降级
        logger.info("向量检索模块不可用（未安装 vector extra）：rag_data.py 加载失败",
                    exc_info=True)
        _rag_data_cache = False
        return None
    return _rag_data_cache


def _rag_data_dir(cfg: Config) -> str:
    """knowledge.rag_data_dir 缺省回退到项目根 rag/。"""
    rag_dir = cfg.get("knowledge.rag_data_dir")
    if rag_dir:
        return str(rag_dir)
    return str(DEFAULT_RAG_ROOT)


class VectorRetriever:
    """向量检索实现（基于 ChromaDB）。"""

    def __init__(self, config: Config | None = None):
        self.config = config or load()
        self._available = None  # 缓存是否可用的状态

    def _check_availability(self) -> bool:
        """检查向量检索是否可用。"""
        if self._available is not None:
            return self._available
        self._available = _load_rag_data_module() is not None
        return self._available

    def search(self, signature: str, query: str = "") -> list[KnowledgeHit]:
        """使用向量检索查找相关游戏知识。"""
        rag_module = _load_rag_data_module()
        if rag_module is None:
            return []

        try:
            rag_data_dir = _rag_data_dir(self.config)
            kb = rag_module.RAGKnowledgeBase(data_root=rag_data_dir)

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
            logger.warning("向量检索失败，回退 SQLite 结果", exc_info=True)
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
            logger.warning("SQLite 知识检索失败", exc_info=True)

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
                        logger.warning("自动索引后向量检索仍失败", exc_info=True)

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

        rag_module = _load_rag_data_module()
        if rag_module is None:
            return
        try:
            kb = rag_module.RAGKnowledgeBase(data_root=_rag_data_dir(self.config))

            # 检查并索引所有游戏
            result = kb.index_all()

            # 只对有索引结果的进行日志输出
            indexed_games = {game: count for game, count in result.items() if count > 0}
            if indexed_games:
                logger.info("自动索引了 %d 个游戏: %s", len(indexed_games), indexed_games)
        except Exception:
            # 自动索引失败时不阻断流程
            logger.warning("RAG 自动索引失败", exc_info=True)


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

    rag_module = _load_rag_data_module()
    if rag_module is None:
        return {}
    try:
        kb = rag_module.RAGKnowledgeBase(data_root=_rag_data_dir(cfg))

        if game_names:
            result = {game: kb.index(game) for game in game_names}
        else:
            result = kb.index_all()

        # 过滤掉索引为 0 的游戏
        return {k: v for k, v in result.items() if v > 0}
    except Exception:
        logger.warning("RAG 索引失败", exc_info=True)
        return {}


def get_available_games() -> list[str]:
    """获取 RAG 数据中可用的游戏列表。"""
    cfg = load()
    games_dir = Path(_rag_data_dir(cfg))
    if not games_dir.exists():
        return []
    try:
        return [d.name for d in games_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        logger.warning("读取 RAG 游戏目录失败：%s", games_dir, exc_info=True)
        return []
