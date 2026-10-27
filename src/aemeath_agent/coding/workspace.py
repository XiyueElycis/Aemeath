"""编程智能体 · 工作区。

一切文件操作都收敛到一个根目录内，越界一律拒绝。这是本模块唯一的"安全边界"，
不依赖调用方自觉：所有工具都只能通过这里拿路径。

刻意**不做 overlay/沙箱副本**：编程智能体需要「改完立刻跑测试」，覆盖层会让
她看不到自己刚写的文件，也会让 shell 命令操作到旧内容；因此改为「真实写入 +
逐次审批」（见 :mod:`.tools` 的审批回调），风险由审批而不是副本兜住。
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from .checkpoints import CheckpointStore

# 目录黑名单：既避免把依赖/缓存扫进来，也避免误改版本库内部状态
_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".idea", ".vscode", "bin", "obj",
}
# 单文件读取上限：再大就不是"看代码"而是"倒垃圾"了
MAX_READ_BYTES = 400_000
# 二进制探测：前若干字节里出现 NUL 就当成二进制跳过
_BINARY_PROBE = 4096


class WorkspaceError(Exception):
    """工作区操作失败（越界、文件不存在、匹配不唯一……）。"""


def _is_probably_binary(path: Path) -> bool:
    """粗判二进制：读开头一小段，含 NUL 或解码失败即视为二进制。"""
    try:
        chunk = path.open("rb").read(_BINARY_PROBE)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        # 只截了一小段，可能是多字节字符被切断；放行让调用方再判断
        return False
    return False


class Workspace:
    """一个受限于 ``root`` 的工作区。

    :param root: 工作区根目录（会被 resolve 成绝对路径）
    """

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"工作区根目录不存在：{self.root}")
        # 检查点跟着工作区走：每次改写前自动存快照，写坏了可以退回去
        self.checkpoints = CheckpointStore(self.root)

    # ------------------------------------------------------------------ #
    # 路径
    # ------------------------------------------------------------------ #
    def resolve(self, path: str) -> Path:
        """把相对路径解析成根目录内的绝对路径；越界直接拒绝。"""
        raw = (path or ".").strip() or "."
        target = Path(raw)
        # 注意：绝对路径也要过一遍校验，不能因为写了绝对路径就放行
        resolved = (self.root / target).resolve() if not target.is_absolute() \
            else target.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise WorkspaceError(
                f"路径「{raw}」在工作区之外（根：{self.root}），已拒绝") from None
        return resolved

    def rel(self, path: Path) -> str:
        """绝对路径 → 相对根的 posix 形式（展示用）。"""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def _guard_writable(self, path: Path) -> None:
        """拒绝写入黑名单目录（版本库内部、依赖、缓存）。"""
        parts = set(path.relative_to(self.root).parts)
        bad = parts & _SKIP_DIRS
        if bad:
            raise WorkspaceError(
                f"「{self.rel(path)}」位于受保护目录（{', '.join(sorted(bad))}），不允许写入")

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    def list_dir(self, path: str = ".", depth: int = 1) -> List[str]:
        """列出目录内容（``深度`` 层，目录名带 ``/``），跳过黑名单目录。"""
        base = self.resolve(path)
        if not base.exists():
            raise WorkspaceError(f"目录不存在：{path}")
        if not base.is_dir():
            raise WorkspaceError(f"不是目录：{path}")
        depth = max(1, min(int(depth), 4))
        out: List[str] = []
        self._walk(base, "", depth, out, limit=300)
        return out

    def _walk(self, directory: Path, prefix: str, depth: int,
              out: List[str], limit: int) -> None:
        if depth <= 0 or len(out) >= limit:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except OSError:
            return
        for entry in entries:
            if len(out) >= limit:
                out.append("…（已截断）")
                return
            if entry.name in _SKIP_DIRS:
                continue
            if entry.is_dir():
                out.append(f"{prefix}{entry.name}/")
                self._walk(entry, f"{prefix}{entry.name}/", depth - 1, out, limit)
            else:
                out.append(f"{prefix}{entry.name}")

    def read_file(self, path: str, start: Optional[int] = None,
                  end: Optional[int] = None) -> str:
        """读文本文件；可选行区间（1 起，含首含尾）。"""
        target = self.resolve(path)
        if not target.exists():
            raise WorkspaceError(f"文件不存在：{path}")
        if target.is_dir():
            raise WorkspaceError(f"「{path}」是目录，不是文件")
        if target.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(
                f"文件过大（{target.stat().st_size} 字节），请用 grep 定位后按行读取")
        if _is_probably_binary(target):
            raise WorkspaceError(f"「{path}」看起来是二进制文件，拒绝读取")
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise WorkspaceError(f"读取失败：{exc}") from exc

        if start is not None or end is not None:
            lo = max(1, int(start or 1))
            hi = min(len(lines), int(end or len(lines)))
            lines = lines[lo - 1:hi]
            # 带行号返回，方便模型后续做精确编辑
            return "\n".join(f"{lo + i:>4}| {t}" for i, t in enumerate(lines))
        if len(lines) > 400:
            head = "\n".join(lines[:400])
            return head + f"\n…（共 {len(lines)} 行，已截断；用 start/end 翻页）"
        return "\n".join(lines)

    def glob(self, pattern: str, limit: int = 100) -> List[str]:
        """按文件名模式匹配（支持 ``**``），跳过黑名单目录。"""
        limit = max(1, min(int(limit), 500))
        hits: List[str] = []
        for path in self._iter_files():
            if fnmatch.fnmatch(path.name, pattern) or \
                    fnmatch.fnmatch(self.rel(path), pattern):
                hits.append(self.rel(path))
                if len(hits) >= limit:
                    break
        return sorted(hits)

    def grep(self, pattern: str, path: str = ".", glob_pattern: str = "",
             limit: int = 60) -> List[str]:
        """正则搜内容，返回 ``文件:行号: 内容``；文件按 glob 过滤。"""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceError(f"正则无效：{exc}") from exc

        base = self.resolve(path)
        files = [base] if base.is_file() else list(self._iter_files(base))
        limit = max(1, min(int(limit), 200))
        hits: List[str] = []
        for file in files:
            if glob_pattern and not fnmatch.fnmatch(
                    self.rel(file), glob_pattern) \
                    and not fnmatch.fnmatch(file.name, glob_pattern):
                continue
            try:
                if file.stat().st_size > MAX_READ_BYTES or _is_probably_binary(file):
                    continue
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{self.rel(file)}:{lineno}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        return hits
        return hits

    def _iter_files(self, base: Optional[Path] = None) -> Iterator[Path]:
        """递归遍历文件（跳过黑名单目录）。"""
        start = base or self.root
        if start.is_file():
            yield start
            return
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    def write_file(self, path: str, content: str) -> str:
        """整文件写入（覆盖），返回相对路径。"""
        target = self.resolve(path)
        self._guard_writable(target)
        if target.is_dir():
            raise WorkspaceError(f"「{path}」是目录，不能用 write_file 覆盖")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self.rel(target)

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        """按精确文本替换一处；``old_text`` 必须唯一命中，否则拒绝。

        要求唯一命中是刻意的：编程智能体最容易出的事故就是"以为改了一处，
        实际改了十处"。命中零处或多处时让它重新读文件再决定。
        """
        target = self.resolve(path)
        self._guard_writable(target)
        if not target.exists():
            raise WorkspaceError(f"文件不存在：{path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_text)
        if count == 0:
            raise WorkspaceError(
                f"在「{path}」里找不到这段原文，请先 read_file 确认当前内容")
        if count > 1:
            raise WorkspaceError(
                f"这段原文在「{path}」里出现 {count} 次，无法确定改哪一处；"
                "请扩大上下文让它唯一")
        target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return self.rel(target)

    # ------------------------------------------------------------------ #
    # 文件组织：建目录 / 移动 / 复制 / 删除
    # ------------------------------------------------------------------ #
    def make_dir(self, path: str) -> str:
        """创建目录（含父级），已存在则视为成功。"""
        target = self.resolve(path)
        self._guard_writable(target)
        target.mkdir(parents=True, exist_ok=True)
        return self.rel(target)

    def _resolve_destination(self, dst: str, source: Path) -> Path:
        """把 ``dst`` 解析成最终落点：已存在的目录表示「移入该目录」。"""
        raw = Path(dst)
        target = self.resolve(dst)
        if target.is_dir():
            target = target / source.name
        if raw.name == "" and str(raw).endswith("/"):
            raise WorkspaceError(f"目标路径不合法：{dst}")
        self._guard_writable(target)
        return target

    def move(self, src: str, dst: str) -> Tuple[str, str]:
        """移动 / 重命名（跨目录、同目录改名都走这里）。"""
        source = self.resolve(src)
        if not source.exists():
            raise WorkspaceError(f"源路径不存在：{src}")
        self._guard_writable(source)
        target = self._resolve_destination(dst, source)
        if target == source:
            raise WorkspaceError("源和目标是同一个路径")
        if target.exists() and not target.is_dir():
            raise WorkspaceError(f"目标已存在：{self.rel(target)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return self.rel(source), self.rel(target)

    def copy(self, src: str, dst: str) -> Tuple[str, str]:
        """复制文件或目录（目录递归复制，跳过黑名单目录）。"""
        source = self.resolve(src)
        if not source.exists():
            raise WorkspaceError(f"源路径不存在：{src}")
        self._guard_writable(source)
        target = self._resolve_destination(dst, source)
        if target == source:
            raise WorkspaceError("源和目标是同一个路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(
                source, target, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*_SKIP_DIRS))
        else:
            shutil.copy2(source, target)
        return self.rel(source), self.rel(target)

    def delete(self, path: str, recursive: bool = False) -> Tuple[str, int]:
        """删除文件或目录；删目录必须显式 ``recursive``。

        返回 ``(相对路径, 受影响的文件数)``——数量用于审批卡片，让用户知道
        这一下会带走多少东西，而不是只看到一个路径。
        """
        target = self.resolve(path)
        if target == self.root:
            raise WorkspaceError("不能删除工作区根目录本身")
        if not target.exists():
            raise WorkspaceError(f"路径不存在：{path}")
        self._guard_writable(target)
        if target.is_dir():
            if not recursive:
                raise WorkspaceError(
                    f"「{path}」是目录：确实要删请传 recursive=true")
            count = sum(1 for _ in self._iter_files(target))
            shutil.rmtree(target)
        else:
            count = 1
            target.unlink()
        return self.rel(target), count


__all__ = ["Workspace", "WorkspaceError"]
