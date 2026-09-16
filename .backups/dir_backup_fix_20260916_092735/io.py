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


# --------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------- #
def read_bytes(session: Optional[SandboxSession], path: PathLike) -> bytes:
    """读取文件字节（沙箱模式 overlay 优先、删除遮蔽、否则透传）。"""
    p = session.map_for_read(path) if session else Path(path)
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    real, virtual, key, rel = session.map_for_write(path, side=side)
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
        Path(path).unlink()
        return
    # 越界校验 + 取 key（根外非 side 删除直接拒绝）
    real, _, key, rel = session.map_for_write(path)
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
    """复制文件。

    :param side_dst: 目标在授权根外（备份类辅助输出），沙箱模式重定向 side。
    :param pristine_src: 源始终读**真实磁盘原件**（备份语义——备份的是原件，
        不是沙箱修改稿），绕过 overlay。
    """
    if session is None:
        dest = Path(dst)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    if pristine_src:
        data = Path(src).read_bytes()
    else:
        data = read_bytes(session, src)
    return write_bytes(session, dst, data, side=side_dst)


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
    "replace_file", "copy_file",
    "exists", "is_dir", "list_dir",
]
