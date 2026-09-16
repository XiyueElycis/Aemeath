"""基于 LangChain + Chroma 的 RAG 游戏问题知识库。

用途
----
为"让模型解决游戏问题"提供检索增强生成（RAG）能力：把某款游戏的排障知识文档
（Markdown）切分、向量化后存入 Chroma，查询时按语义召回片段，再由 DeepSeek
基于召回内容生成答案，从而减少幻觉、给出贴近该游戏真实修复方案的回复。

与项目内 SQLite 知识库（``gamedoctor.knowledge``）互补：SQLite 走"错误签名 →
修复模板"的精确/模糊匹配，本模块走"自然语言 → 知识片段"的语义召回，产物可
直接拼进 :class:`~gamedoctor.solver.generator.LLMSolver` 的 prompt。

多游戏适配
----------
通过 :class:`GameKnowledgeAdapter` 统一接口，每款游戏对应一个适配器实例，拥有
独立的文档目录与向量集合，互不干扰。游戏数量不定，接入新游戏只需其一：

1. 在数据根目录下新建同名子目录并放入 ``*.md``（走默认 Markdown 适配器）；
2. 或注册一个自定义 :class:`GameKnowledgeAdapter`（例如对接 wiki / 数据库）。

典型用法::

    kb = RAGKnowledgeBase(data_root="rag/games")
    kb.index_all()                              # 为所有游戏建索引
    chunks = kb.search("黑神话", "启动报 0xc000007b")
    answer = kb.ask("黑神话", "启动报 0xc000007b 怎么解决？")
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.embeddings import HuggingFaceBgeEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

# --------------------------------------------------------------------------- #
# 全局配置
# --------------------------------------------------------------------------- #

# DeepSeek API Key：优先读环境变量，未配置时用内置默认值（仅本地调试，勿提交真实密钥）。
_DEFAULT_API_KEY = "sk-24ed998e751e465f9de1f9e41c754ca1"
os.environ.setdefault("DEEPSEEK_API_KEY", _DEFAULT_API_KEY)

# 数据根目录：本模块所在目录（rag/），每款游戏在其下拥有一个同名子目录存放 Markdown 文档。
DEFAULT_DATA_ROOT = Path(__file__).resolve().parent

# 中文友好的 BGE 嵌入模型；可用环境变量覆盖（如换用加大版提升质量）。
BGE_MODEL_NAME = os.getenv("BGE_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

# Markdown 切分参数：块大小与重叠（中文排障文档单条解决方案通常较短，块不宜过大）。
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# 默认检索返回片段数。
DEFAULT_TOP_K = 4


@lru_cache(maxsize=1)
def get_default_embeddings() -> Embeddings:
    """返回默认中文 BGE 嵌入模型实例（懒加载 + 缓存，避免重复加载模型权重）。"""
    return HuggingFaceBgeEmbeddings(model_name=BGE_MODEL_NAME)


# --------------------------------------------------------------------------- #
# 结果结构
# --------------------------------------------------------------------------- #


@dataclass
class KnowledgeChunk:
    """一条被召回的知识片段。"""

    content: str      # 片段正文
    source: str       # 来源文件名
    score: float      # 相关性得分（Chroma 的 L2 距离，值越小越相关）
    game: str         # 所属游戏


# --------------------------------------------------------------------------- #
# 单款游戏适配器（预留接口，可扩展多款游戏 / 不同知识源）
# --------------------------------------------------------------------------- #


class GameKnowledgeAdapter(ABC):
    """单款游戏知识库适配器接口。

    所有游戏复用同一套能力（建索引 / 检索 / 生成），实现细节各自封装。
    若需接入非 Markdown 知识源（如在线 wiki、自建数据库），继承本类并实现
    :meth:`index` 与 :meth:`search` 即可。
    """

    def __init__(self, game: str):
        self.game = game

    @abstractmethod
    def index(self, force: bool = False) -> int:
        """构建 / 重建该游戏的向量索引，返回入库的文档片段数。"""

    @abstractmethod
    def search(self, query: str, k: int = DEFAULT_TOP_K) -> list[KnowledgeChunk]:
        """按语义召回最相关的 ``k`` 条知识片段。"""


class MarkdownGameAdapter(GameKnowledgeAdapter):
    """默认适配器：把某游戏目录下的 Markdown 文档建成向量库。

    目录约定：``<docs_dir>/**/*.md``；向量库持久化到 ``<persist_dir>``，重复
    建索引时直接复用，避免每次重新嵌入。``force=True`` 可强制重建。
    """

    def __init__(
        self,
        game: str,
        docs_dir: str | Path,
        persist_dir: str | Path | None = None,
        embeddings: Embeddings | None = None,
    ):
        super().__init__(game)
        self.docs_dir = Path(docs_dir)
        self.persist_dir = Path(persist_dir or self.docs_dir.parent / ".vectorstore" / game)
        self._embeddings = embeddings  # None 时在真正使用时懒加载默认 BGE

    # -- 内部工具 ---------------------------------------------------------- #
    @property
    def _embedder(self) -> Embeddings:
        # 惰性解析嵌入模型：只有真正建索引 / 检索时才加载，并支持多游戏共享同一实例
        return self._embeddings or get_default_embeddings()

    def _splitter(self) -> RecursiveCharacterTextSplitter:
        # 追加中文标点作分隔符，避免长段落被硬切成语义不全的片段
        return RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
        )

    def _load_documents(self) -> list:
        """递归加载 Markdown 文档；目录不存在时返回空列表。"""
        if not self.docs_dir.exists():
            return []
        loader = DirectoryLoader(
            str(self.docs_dir),
            glob="**/*.md",
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8"},
        )
        return loader.load()

    def _count_persisted(self) -> int:
        store = Chroma(
            persist_directory=str(self.persist_dir),
            embedding_function=self._embedder,
        )
        # 使用 Chroma 私有接口仅作"已建索引"时的计数提示，不参与检索逻辑
        return int(store._collection.count())  # noqa: SLF001

    # -- 对外接口 ---------------------------------------------------------- #
    def index(self, force: bool = False) -> int:
        """切分文档并持久化向量库，返回入库片段数。

        已存在持久化目录且非 ``force`` 时跳过重建，直接返回已有片段数。
        """
        if not force and self.persist_dir.exists():
            return self._count_persisted()

        documents = self._load_documents()
        if not documents:
            return 0
        chunks = self._splitter().split_documents(documents)
        # 写入游戏名元数据，便于跨游戏检索时过滤与结果归因
        for chunk in chunks:
            chunk.metadata.setdefault("game", self.game)
        Chroma.from_documents(
            documents=chunks,
            embedding=self._embedder,
            persist_directory=str(self.persist_dir),
        )
        return len(chunks)

    def search(self, query: str, k: int = DEFAULT_TOP_K) -> list[KnowledgeChunk]:
        """语义检索：返回相关性降序的知识片段（未建索引时返回空列表）。"""
        if not self.persist_dir.exists():
            return []
        store = Chroma(
            persist_directory=str(self.persist_dir),
            embedding_function=self._embedder,
        )
        results = store.similarity_search_with_relevance_scores(query, k=k)
        return [
            KnowledgeChunk(
                content=doc.page_content,
                source=Path(doc.metadata.get("source", "")).name,
                score=float(score),
                game=self.game,
            )
            for doc, score in results
        ]


# --------------------------------------------------------------------------- #
# 多游戏知识库管理器（适配器注册表）
# --------------------------------------------------------------------------- #


class RAGKnowledgeBase:
    """多游戏知识库管理器，维护 ``game -> GameKnowledgeAdapter`` 注册表。

    未显式注册的游戏，在首次访问时按默认目录约定自动创建一个 Markdown 适配器。
    """

    def __init__(
        self,
        data_root: str | Path = DEFAULT_DATA_ROOT,
        embeddings: Embeddings | None = None,
    ):
        self.data_root = Path(data_root)
        self._embeddings = embeddings
        self._adapters: dict[str, GameKnowledgeAdapter] = {}

    def register(self, adapter: GameKnowledgeAdapter) -> "RAGKnowledgeBase":
        """注册一个自定义游戏适配器，返回 self 以便链式调用。"""
        self._adapters[adapter.game] = adapter
        return self

    def adapter_for(self, game: str) -> GameKnowledgeAdapter:
        """返回指定游戏的适配器；未注册时按默认目录约定自动创建。"""
        if game not in self._adapters:
            self._adapters[game] = MarkdownGameAdapter(
                game=game,
                docs_dir=self.data_root / game,
                persist_dir=self.data_root / ".vectorstore" / game,
                embeddings=self._embeddings,
            )
        return self._adapters[game]

    def index(self, game: str, force: bool = False) -> int:
        """为单一游戏建立索引。"""
        return self.adapter_for(game).index(force=force)

    def index_all(self, force: bool = False) -> dict[str, int]:
        """为数据根目录下所有游戏子目录建立索引，返回 ``{游戏名: 片段数}``。"""
        if not self.data_root.exists():
            return {}
        result: dict[str, int] = {}
        # 每个子目录视为一款游戏；跳过隐藏目录（如 .vectorstore）
        for entry in sorted(self.data_root.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                result[entry.name] = self.index(entry.name, force=force)
        return result

    def search(self, game: str, query: str, k: int = DEFAULT_TOP_K) -> list[KnowledgeChunk]:
        """在指定游戏的知识库中检索。"""
        return self.adapter_for(game).search(query, k=k)

    def build_context(self, game: str, query: str, k: int = DEFAULT_TOP_K) -> str:
        """把检索到的片段拼成一段可塞进 prompt 的上下文。"""
        chunks = self.search(game, query, k=k)
        return "\n\n".join(
            f"[{i}]（来源 {c.source}）{c.content}" for i, c in enumerate(chunks, 1)
        )

    def ask(
        self,
        game: str,
        question: str,
        k: int = DEFAULT_TOP_K,
        model: str = "deepseek-chat",
    ) -> str:
        """检索 + 生成：用召回片段作上下文，让 DeepSeek 生成限定于知识库的答案。"""
        context = self.build_context(game, question, k=k)
        if not context:
            return "（未检索到该游戏的相关知识，请先放入 Markdown 文档并执行索引。）"

        llm = ChatDeepSeek(model=model, temperature=0)
        # 用系统提示约束模型只基于给定片段作答，避免脱离知识库的臆造
        messages = [
            SystemMessage(
                content=(
                    "你是游戏排障助手。请严格依据提供的游戏知识片段回答用户问题，"
                    "只采用片段中已说明的解决方案，不要臆造。若片段不足以解决，"
                    "请如实说明并建议补充资料。"
                )
            ),
            HumanMessage(content=f"参考资料：\n{context}\n\n用户问题：{question}"),
        ]
        response = llm.invoke(messages)
        return str(response.content)