"""编程智能体 · 仓库地图（repo map）。

给模型一张「这个仓库里有些什么」的廉价概览：文件名 + 行数 + 关键符号所在行。
不建索引、不落盘、不依赖 embedding，一次扫描即可——目的是让模型少走
"盲 grep 十次才找到入口"的弯路，而不是替代精确检索。

符号抽取用逐语言的正则：够粗但便宜。宁可漏掉一些私有小函数，也不要为了
准确度引入语法解析器这个重依赖。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from .workspace import Workspace

# 每种扩展名对应一组「符号正则」，第二条捕获组是名字
_SYMBOLS: Dict[str, Tuple[str, ...]] = {
    ".py": (r"^(?:class|def|async\s+def)\s+(\w+)",),
    ".js": (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            r"^(?:export\s+)?class\s+(\w+)",
            r"^(?:export\s+)?const\s+(\w+)\s*="),
    ".ts": (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)",
            r"^(?:export\s+)?(?:const|let)\s+(\w+)\s*=",
            r"^(?:export\s+)?interface\s+(\w+)"),
    ".tsx": (r"^(?:export\s+)?(?:default\s+)?function\s+(\w+)",
             r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)",
             r"^(?:export\s+)?interface\s+(\w+)"),
    ".cs": (r"\b(?:class|record|struct|interface|enum)\s+(\w+)",),
    ".go": (r"^func\s+(?:\(\w+\s+\*\w+\)\s+)?(\w+)", r"^type\s+(\w+)"),
    ".rs": (r"^(?:pub\s+)?fn\s+(\w+)", r"^(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"),
    ".java": (r"\b(?:class|interface|enum)\s+(\w+)",),
}

# 没有符号概念的扩展名不参与扫描
_TEXT_EXT = {".md", ".txt", ".json", ".toml", ".cfg", ".ini", ".yaml", ".yml"}

MAX_FILES = 300
MAX_SYMBOLS_PER_FILE = 30
MAX_LINES_SCAN = 3_000


def _symbols_of(path: Path) -> Tuple[int, List[str]]:
    """返回 (总行数, ["12: def foo", ...])；读不了就返回 (0, [])。"""
    ext = path.suffix.lower()
    patterns = _SYMBOLS.get(ext)
    if not patterns:
        return 0, []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, []
    lines = text.splitlines()
    found: List[str] = []
    for idx, line in enumerate(lines[:MAX_LINES_SCAN], 1):
        for pattern in patterns:
            match = re.match(pattern, line)
            if match:
                found.append(f"{idx}: {match.group(1)}")
                break
        if len(found) >= MAX_SYMBOLS_PER_FILE:
            found.append("…")
            break
    return len(lines), found


def build_repo_map(workspace: Workspace, path: str = ".",
                   max_files: int = MAX_FILES) -> str:
    """生成仓库地图文本。

    输出形如::

        src/pkg/core.py  (120 行)
          - 8: add
          - 20: Parser

    按文件路径排序，便于模型按目录找东西。
    """
    base = workspace.resolve(path)
    max_files = max(1, min(int(max_files), 1000))

    files: List[Path] = []
    if base.is_file():
        files = [base]
    else:
        for candidate in workspace._iter_files(base):  # noqa: SLF001 — 同包内协作
            if candidate.suffix.lower() in _SYMBOLS or \
                    candidate.suffix.lower() in _TEXT_EXT:
                files.append(candidate)
            if len(files) >= max_files:
                break

    if not files:
        return "（这个范围内没有可索引的源文件）"

    blocks: List[str] = []
    for file in sorted(files, key=lambda p: workspace.rel(p)):
        lines, symbols = _symbols_of(file)
        rel = workspace.rel(file)
        if not symbols:
            blocks.append(f"{rel}  ({lines} 行)" if lines else rel)
            continue
        body = "\n".join(f"  - {s}" for s in symbols)
        blocks.append(f"{rel}  ({lines} 行)\n{body}")
    return "\n".join(blocks)


__all__ = ["build_repo_map"]
