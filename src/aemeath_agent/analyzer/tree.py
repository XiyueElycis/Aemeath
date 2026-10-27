"""游戏目录巡检（PRD §4.2 只读体检）：一次有界递归，产出子目录清单。

治理链路此前只拿得到「顶层文件名」这一层信息（引擎识别只 ``iterdir`` 一次），
遇到 UE 这类把引擎与内容都放在子目录里的游戏（``Client/Binaries/Win64``、
``Engine/...``）就什么也看不见。本模块给出一次**只读、有界**的目录树巡检：

- 深度上限 :data:`_MAX_DEPTH`：清单定义为「根目录往下 N 层」，这是口径不是异常；
- 数量上限 :data:`_MAX_DIRS` / :data:`_MAX_FILES`：触顶时把 ``truncated`` 置真，
  清单宁可标注"不完整"，也不冒充完整；
- 每个子目录同时给出**直属**文件/子目录数与**含子树累计**的文件数/体积；
- 不追符号链接与 Windows 目录联接（会形成环）；
- 全程只读：``os.scandir`` 列名字，唯一年 ``entry.stat()`` 用于体积统计。

体积统计与目录清单合并成一次遍历（原先 ``context_builder._dir_size_mb``
是独立的一次 ``os.walk``，同一棵树走两遍纯属浪费）。
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Tuple

# 清单口径：根目录往下 3 层（足够覆盖 Client/Binaries/Win64、Engine/Binaries 等）
_MAX_DEPTH = 3
# 资源护栏：目录数与文件数上限，防止超大安装目录把一轮对话拖死
_MAX_DIRS = 300
_MAX_FILES = 20000
# 顶层文件清单上限（供引擎/平台判断与提示词参考）
_MAX_TOP_FILES = 40


@dataclass(frozen=True)
class DirEntry:
    """一个被巡检到的子目录。"""

    name: str
    rel_path: str        # 相对根目录，"/" 分隔
    depth: int           # 相对根目录的层级，顶层子目录为 1
    file_count: int      # 直属文件数
    subdir_count: int    # 直属子目录数
    total_files: int     # 含子树的累计文件数
    total_size_mb: int   # 含子树的累计体积（MiB）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "rel_path": self.rel_path,
            "depth": self.depth,
            "file_count": self.file_count,
            "subdir_count": self.subdir_count,
            "total_files": self.total_files,
            "total_size_mb": self.total_size_mb,
        }


@dataclass(frozen=True)
class TreeInventory:
    """巡检结果。``dirs`` 按 (深度, 路径) 稳定排序，``truncated`` 表示受数量上限截断。"""

    root: str
    dirs: Tuple[DirEntry, ...] = ()
    total_dirs: int = 0
    file_count: int = 0
    size_mb: int = 0
    top_files: Tuple[str, ...] = ()
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "total_dirs": self.total_dirs,
            "file_count": self.file_count,
            "size_mb": self.size_mb,
            "top_files": list(self.top_files),
            "truncated": self.truncated,
            "dirs": [d.to_dict() for d in self.dirs],
        }

    def top_level(self) -> Tuple[DirEntry, ...]:
        """顶层子目录（深度 1）。"""
        return tuple(d for d in self.dirs if d.depth == 1)

    def children(self, entry: DirEntry) -> Tuple[DirEntry, ...]:
        """``entry`` 的直属子目录（按路径前缀判定，不依赖排序）。"""
        prefix = f"{entry.rel_path}/"
        return tuple(
            d for d in self.dirs
            if d.depth == entry.depth + 1 and d.rel_path.startswith(prefix)
        )


def scan_tree(
    root: str | Path | None,
    *,
    max_depth: int = _MAX_DEPTH,
    max_dirs: int = _MAX_DIRS,
    max_files: int = _MAX_FILES,
    max_top_files: int = _MAX_TOP_FILES,
) -> TreeInventory:
    """有界递归巡检 ``root``，返回子目录清单与体积统计（只读，绝不抛异常）。"""
    base = Path(root) if root else None
    if base is None or not base.is_dir():
        return TreeInventory(root=str(root or ""))

    nodes: Dict[str, Dict[str, Any]] = {}   # rel_path -> {name/depth/files/subdirs/size}
    queue: Deque[Tuple[Path, str, int]] = deque([(base, "", 0)])
    top_files: List[str] = []
    file_count = 0
    total_bytes = 0
    truncated = False

    while queue:
        path, rel, depth = queue.popleft()
        node: Dict[str, Any] = {"name": path.name, "depth": depth,
                                "files": 0, "subdirs": 0, "size": 0}
        nodes[rel] = node
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            # 权限不足 / 目录被删：记为已访问但无内容，不阻断巡检
            continue

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    node["subdirs"] += 1
                    if depth >= max_depth:
                        continue          # 深度口径边界，不算截断
                    if len(nodes) + len(queue) >= max_dirs:
                        truncated = True
                        continue
                    queue.append((Path(entry.path), _join(rel, entry.name), depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    node["files"] += 1
                    if not rel and len(top_files) < max_top_files:
                        top_files.append(entry.name)
                    if file_count >= max_files:
                        truncated = True
                        continue
                    file_count += 1
                    try:
                        total_bytes += entry.stat().st_size
                    except OSError:
                        continue          # 读不到大小只影响体积，不影响计数
            except OSError:
                continue

    # 自底向上把子树计数累加到父目录
    sub_files = {rel: n["files"] for rel, n in nodes.items()}
    sub_size = {rel: n["size"] for rel, n in nodes.items()}
    for rel in sorted(nodes, key=lambda r: -nodes[r]["depth"]):
        parent = _parent_rel(rel)
        if parent in nodes:
            sub_files[parent] += sub_files[rel]
            sub_size[parent] += sub_size[rel]

    dirs = tuple(
        DirEntry(
            name=nodes[rel]["name"],
            rel_path=rel,
            depth=nodes[rel]["depth"],
            file_count=nodes[rel]["files"],
            subdir_count=nodes[rel]["subdirs"],
            total_files=sub_files[rel],
            total_size_mb=_to_mb(sub_size[rel]),
        )
        for rel in sorted((r for r in nodes if r),
                          key=lambda r: (nodes[r]["depth"], r))
    )
    return TreeInventory(
        root=str(base),
        dirs=dirs,
        total_dirs=len(dirs),
        file_count=file_count,
        size_mb=_to_mb(total_bytes),
        top_files=tuple(top_files),
        truncated=truncated,
    )


def _join(rel: str, name: str) -> str:
    return f"{rel}/{name}" if rel else name


def _parent_rel(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _to_mb(size_bytes: int) -> int:
    return round(size_bytes / (1024 * 1024))