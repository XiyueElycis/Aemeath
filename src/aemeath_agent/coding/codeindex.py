"""编程智能体 · 代码索引（语义检索）。

``grep`` 只能精确匹配字面量；用户问「哪里处理了登录」时，模型往往要试好几次
才碰上正确的关键词。这里把源文件切成小块，用与记忆检索同一套可插拔后端
（:mod:`aemeath_agent.retrieval`）做相关度排序。

索引**不落盘**：工作区是随时在改的，持久化索引反而会给出陈旧结果；一次任务
里几十个文件的分块编码成本可以忽略（本地哈希实现是纯 CPU 的线性扫描）。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from ..retrieval import Retriever, build_retriever
from .workspace import Workspace

# 每个分块最多多少行：太小丢了上下文，太大让命中点变模糊
CHUNK_LINES = 40
# 单块最大字符数（防止压缩后的长行把整块撑爆）
CHUNK_CHARS = 2_000
# 参与索引的最大文件数
MAX_FILES = 400
# 只索引这些扩展名（其余多半是数据/二进制，检索价值低）
CODE_EXT = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".cs", ".xaml", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp",
    ".hpp", ".rb", ".php", ".swift", ".sql", ".sh", ".ps1", ".toml",
    ".json", ".yaml", ".yml", ".md",
}


def _chunks(workspace: Workspace) -> List[tuple[str, int, str]]:
    """把工作区切成 (相对路径, 起始行, 文本) 三元组。"""
    out: List[tuple[str, int, str]] = []
    for path in workspace._iter_files():            # noqa: SLF001 — 同包内协作
        if path.suffix.lower() not in CODE_EXT:
            continue
        try:
            if path.stat().st_size > 400_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = workspace.rel(path)
        for start in range(0, len(lines), CHUNK_LINES):
            block = "\n".join(lines[start:start + CHUNK_LINES])
            if not block.strip():
                continue
            out.append((rel, start + 1, block[:CHUNK_CHARS]))
        if len(out) >= MAX_FILES * 8:
            break
    return out


def search_code(workspace: Workspace, query: str, *, limit: int = 5,
                retriever: Optional[Retriever] = None) -> List[str]:
    """返回最相关的代码片段（含路径与起始行号），供模型直接接着读。

    退化行为：检索不到任何命中时，回落到 ``grep`` 的字面匹配，避免"问什么都
    说没有"——那会让模型误以为仓库里真的没有这段逻辑。
    """
    limit = max(1, min(int(limit), 20))
    blocks = _chunks(workspace)
    if not blocks:
        return []

    docs = [f"{rel}\n{text}" for rel, _line, text in blocks]
    backend = retriever or build_retriever()
    picked = backend.rank(query, docs, top_k=limit)

    hits: List[str] = []
    for idx in picked:
        rel, line, text = blocks[idx]
        hits.append(f"{rel}:{line}\n{text}")
    if hits:
        return hits

    # 语义排序没命中：退回字面搜索（模型至少能拿到一条线索）
    literal = workspace.grep(query, limit=limit * 4)
    return literal[:limit]


__all__ = ["search_code", "CHUNK_LINES", "CODE_EXT"]
