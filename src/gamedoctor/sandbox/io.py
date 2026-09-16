"""沙箱感知的统一文件原语。

所有可能落盘的智能体（script_editor / web_search / game_content /
installation）都只经本模块操作文件，规则只有一条：

- ``session is None``（直接访问模式）：等价直接操作真实路径，行为与改造前
  逐字节一致；
- ``session`` 非 None（沙箱授权模式）：读走 overlay→真实透传映射，写/删全部
  重定向进沙箱并记录变更，真实文件系统在 apply 前零变化。

智能体拿到的路径始终是**真实路径语义**（相对 ``game_dir`` 解析的结果），
沙箱映射在本层内部完成，因此调用方代码几乎不需要条件分支。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .session import ChangeOp, SandboxSession, SandboxViolation, _TEXT_SUFFIXES

PathLike = Union[str, Path]


def _reject_dir(path: Path, *, what: str) -> None:
    """目录走文件原语时给明确错误（Windows 原生只报 Permission denied，易误诊）。"""
    if path.is_dir():
        raise IsADirectoryError(f"{what}是目录而非文件，文件原语不支持目录：{path}")


# --------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------- #
def read_bytes(session: Optional[SandboxSession], path: PathLike) -> bytes:
    """读取文件字节（沙箱模式 overlay 优先、删除遮蔽、否则透传）。"""
    p = session.map_for_read(path) if session else Path(path)
    _reject_dir(p, what="读取目标")
    return p.read_bytes()


def read_text(session: Optional[SandboxSession], path: PathLike) -> str:
    """按候选编码读取文本（utf-8-sig/utf-8/gbk/latin-1 兜底）。"""
    raw = read_bytes(session, path)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------- #
def _is_text_path(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_SUFFIXES


def write_bytes(session: Optional[SandboxSession], path: PathLike, data: bytes,
                *, op: Optional[str] = None, side: bool = False,
                is_text: Optional[bool] = None) -> Path:
    """写字节。返回实际写入路径（沙箱模式为 overlay/side 内虚拟路径）。

    :param op: 显式变更类型（如下载用 ``download``）；缺省按真实文件是否
        存在自动判定 create/modify。
    :param side: 声明这是授权根外辅助输出（备份等），映射到 side 区。
    """
    if session is None:
        target = Path(path)
        _reject_dir(target, what="写入目标")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    real, virtual, key, rel = session.map_for_write(path, side=side)
    if real.is_dir():
        raise IsADirectoryError(f"写入目标已被同名目录占用，无法按文件写入：{real}")
    if side and op is None:
        op = ChangeOp.SIDE_WRITE
    virtual.parent.mkdir(parents=True, exist_ok=True)
    virtual.write_bytes(data)
    session.note_write(
        real, virtual, key, rel, len(data),
        op=op, is_text=_is_text_path(real) if is_text is None else is_text)
    return virtual


def write_text(session: Optional[SandboxSession], path: PathLike, text: str,
               *, op: Optional[str] = None, side: bool = False) -> Path:
    """写文本（统一 UTF-8，与 script_editor 原口径一致）。"""
    return write_bytes(session, path, text.encode("utf-8"),
                       op=op, side=side, is_text=True)


def remove(session: Optional[SandboxSession], path: PathLike) -> None:
    """删除文件（沙箱模式记 tombstone，真实文件不动）。"""
    if session is None:
        target = Path(path)
        _reject_dir(target, what="删除目标")
        target.unlink()
        return
    # 越界校验 + 取 key（根外非 side 删除直接拒绝）
    real, _, key, rel = session.map_for_write(path)
    if real.is_dir():
        raise IsADirectoryError(f"删除目标是目录，文件删除原语不支持目录：{real}")
    session.note_delete(real, key, rel)


def ensure_dir(session: Optional[SandboxSession], path: PathLike,
               *, side: bool = False) -> Path:
    """确保目录存在（沙箱模式在 overlay/side 建目录并记 mkdir 变更）。"""
    if session is None:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    real, virtual, key, rel = session.map_for_write(path, side=side)
    virtual.mkdir(parents=True, exist_ok=True)
    if key not in session.changes and key not in session.tombstones:
        session.note_write(real, virtual, key, rel, None,
                           op=ChangeOp.MKDIR, is_text=False)
    return virtual


def replace_file(session: Optional[SandboxSession], target: PathLike,
                 source: PathLike) -> Path:
    """用 source 内容覆盖 target（沙箱模式：读映射 source、写重定向 target）。"""
    data = read_bytes(session, source)
    return write_bytes(session, target, data)


def copy_file(session: Optional[SandboxSession], src: PathLike, dst: PathLike,
              *, side_dst: bool = False, pristine_src: bool = False) -> Path:
    """复制文件（源必须是文件；目录请用 :func:`copy_path`）。

    :param side_dst: 目标在授权根外（备份类辅助输出），沙箱模式重定向 side。
    :param pristine_src: 源始终读**真实磁盘原件**（备份语义——备份的是原件，
        不是沙箱修改稿），绕过 overlay。
    :raises IsADirectoryError: 源是目录（Windows 原生 copy2 只会报误导性的
        Permission denied，这里显式区分类型错误与权限错误）。
    """
    source = Path(src)
    _reject_dir(source, what="复制源")
    if session is None:
        dest = Path(dst)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return dest

    if pristine_src:
        data = source.read_bytes()
    else:
        data = read_bytes(session, src)
    return write_bytes(session, dst, data, side=side_dst)


def copy_path(session: Optional[SandboxSession], src: PathLike, dst: PathLike,
              *, side_dst: bool = False, pristine_src: bool = False) -> Path:
    """复制文件**或目录**（目录递归快照），用于备份可能为目录的目标。

    与 :func:`copy_file` 参数语义一致；区别仅在于源为目录时：
    - 直接模式：``shutil.copytree`` 整体复制（保留空目录）；
    - 沙箱模式：不复制真实根，按目录树逐文件重定向到 overlay/side，每个文件
      各记一条变更，落盘时随 apply 统一写回。

    :raises FileNotFoundError: 源路径不存在。
    """
    source = Path(src)
    if not source.exists():
        raise FileNotFoundError(f"复制源不存在：{source}")

    if session is None:
        dest = Path(dst)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            shutil.copy2(source, dest)
        return dest

    if not source.is_dir():
        data = source.read_bytes() if pristine_src else read_bytes(session, source)
        return write_bytes(session, dst, data, side=side_dst)

    # 目录：先建目标根（保留源为空目录的语义），再逐文件重定向
    dest = Path(dst)
    ensure_dir(session, dest, side=side_dst)
    for child in sorted(source.rglob("*")):
        rel = child.relative_to(source)
        if child.is_dir():
            ensure_dir(session, dest / rel, side=side_dst)
        else:
            data = child.read_bytes() if pristine_src else read_bytes(session, child)
            write_bytes(session, dest / rel, data, side=side_dst)
    return dest


# --------------------------------------------------------------------- #
# 元信息 / 目录列举
# --------------------------------------------------------------------- #
def exists(session: Optional[SandboxSession], path: PathLike) -> bool:
    return session.exists(path) if session else Path(path).exists()


def is_dir(session: Optional[SandboxSession], path: PathLike) -> bool:
    return session.is_dir(path) if session else Path(path).is_dir()


def list_dir(session: Optional[SandboxSession], path: PathLike) -> List[Dict[str, Any]]:
    """列目录（沙箱模式返回真实+overlay 合并视图），条目含 name/path/is_dir/size。"""
    if session is None:
        p = Path(path)
        return [
            {"name": c.name, "path": str(c), "is_dir": c.is_dir(),
             "size": c.stat().st_size if c.is_file() else None}
            for c in sorted(p.iterdir())
        ]
    return session.list_dir(path)


__all__ = [
    "SandboxViolation",
    "read_bytes", "read_text",
    "write_bytes", "write_text", "remove", "ensure_dir",
    "replace_file", "copy_file", "copy_path",
    "exists", "is_dir", "list_dir",
]
