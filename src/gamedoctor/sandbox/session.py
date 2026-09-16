"""票据级副本沙箱会话。

核心语义（"读透传 / 写重定向 / 删除标记"）：

- 沙箱根：``~/.gamedoctor/sandbox/<ticket_id>/``
  - ``overlay/``：授权根（游戏目录）内写入的覆盖层，保持相对目录结构；
  - ``side/``：授权根之外辅助输出（各智能体自发备份、存档备份等）的重定向区；
  - ``meta.json``：会话状态与变更清单（原子写，后端重启后可恢复）。
- **读**：文件在 overlay 中存在则读 overlay，否则透传真实文件；被删除文件
  （tombstone）视为不存在。
- **写**：根内 create/edit/replace/download 全部重定向到 overlay；delete 只
  记 tombstone 不动真实文件；根外写入仅允许 ``side=True`` 的辅助输出（智能体
  备份等），其他跨根绝对路径一律 :class:`SandboxViolation`。
- **落盘**：:meth:`SandboxSession.apply_changes` 先把将被破坏的真实文件快照
  到 ``~/.gamedoctor/backups/apply_<ticket_id>/``，再逐项应用；任何一项失败
  不中断其余项，会话保持 ready 可重试。

设计约束：初始化**不复制**真实根（游戏目录可能数十 GB），仅被触碰文件按需
进入 overlay。
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import app_dir

META_NAME = "meta.json"


class SandboxViolation(Exception):
    """目标路径逃逸授权根（路径穿越 / 跨根写入），操作被拒绝。"""


class SandboxStateError(Exception):
    """会话当前状态不允许该操作（如 running 状态不允许 apply）。"""


# --------------------------------------------------------------------- #
# 变更类型
# --------------------------------------------------------------------- #
class ChangeOp:
    CREATE = "create"          # 真实位置原本不存在的新文件
    MODIFY = "modify"          # 覆盖真实位置已有文件
    DELETE = "delete"          # 删除真实位置文件（tombstone）
    DOWNLOAD = "download"      # 下载落地（语义同写，单独标记供 UI 区分）
    MKDIR = "mkdir"            # 建目录（apply 时在真实位置创建）
    SIDE_WRITE = "side_write"  # 授权根外辅助输出（备份等），落盘时写回原位


class SandboxStatus:
    RUNNING = "running"
    READY = "ready"
    APPLIED = "applied"
    DISCARDED = "discarded"
    APPLY_FAILED = "apply_failed"


@dataclass
class Change:
    """单文件变更记录。

    :param key: 变更清单唯一键：根内为相对路径（posix 分隔），根外为
        ``@side/`` 前缀的规整名。
    :param real_path: 原始真实绝对路径（apply 的目标）。
    :param virtual_path: 沙箱内实际产物路径（delete 可为空）。
    :param op: :class:`ChangeOp` 常量。
    :param backup_path: apply 时该文件真实原件的备份路径（未应用前为空）。
    """

    key: str
    relpath: str
    real_path: str
    op: str
    virtual_path: str = ""
    size: Optional[int] = None
    is_text: bool = True
    backup_path: str = ""
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()


def _side_parts(p: Path) -> List[str]:
    """把根外绝对路径规整为 side 区相对段，如 ``C:\\Users\\x\\a.b`` →
    ``["drive-C", "Users", "x", "a.b"]``；无盘符（POSIX）→ ``drive-root/...``。"""
    drive = (p.drive or "").rstrip(":") or "root"
    # Windows 的 p.parts 首段是锚点（如 "C:\\"），剔除后保留各目录名
    tail = [seg for seg in p.parts if seg not in (p.anchor, "/", "\\")]
    return [f"drive-{drive}", *tail]


class SandboxSession:
    """一张票据对应一个沙箱会话（外部按 ticket_id 创建/加载）。"""

    def __init__(self, ticket_id: str, real_root: Optional[Path | str] = None,
                 sandbox_base: Optional[Path | str] = None):
        self.ticket_id = ticket_id
        self.root = Path(sandbox_base or (app_dir() / "sandbox")) / ticket_id
        self.overlay_dir = self.root / "overlay"
        self.side_dir = self.root / "side"
        # real_root 允许为空（无游戏目录的纯辅助任务，此时一切写入只能走 side）
        self.real_root: Optional[Path] = Path(real_root).resolve() if real_root else None
        self.status: str = SandboxStatus.RUNNING
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self.applied_at: Optional[str] = None
        self.changes: Dict[str, Change] = {}
        self.tombstones: set[str] = set()
        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        self.side_dir.mkdir(parents=True, exist_ok=True)
        self.save()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def save(self) -> None:
        """原子写 meta.json（tmp + replace，避免崩溃时留下半份清单）。"""
        self.updated_at = datetime.now().isoformat()
        payload = {
            "ticket_id": self.ticket_id,
            "real_root": str(self.real_root) if self.real_root else "",
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "applied_at": self.applied_at,
            "changes": {k: asdict(v) for k, v in self.changes.items()},
            "tombstones": sorted(self.tombstones),
        }
        tmp = self.root / f"{META_NAME}.tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.root / META_NAME)

    @classmethod
    def load(cls, ticket_id: str, sandbox_base: Optional[Path | str] = None) -> "SandboxSession":
        """按 ticket_id 从磁盘恢复会话（含 ready/apply_failed/applied 等状态）。"""
        root = Path(sandbox_base or (app_dir() / "sandbox")) / ticket_id
        meta_path = root / META_NAME
        if not meta_path.exists():
            raise FileNotFoundError(f"沙箱会话不存在：{ticket_id}")
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        sess = cls.__new__(cls)  # 跳过 __init__ 的建目录逻辑
        sess.ticket_id = data["ticket_id"]
        sess.root = root
        sess.overlay_dir = root / "overlay"
        sess.side_dir = root / "side"
        rr = data.get("real_root") or ""
        sess.real_root = Path(rr).resolve() if rr else None
        sess.status = data["status"]
        sess.created_at = data.get("created_at", "")
        sess.updated_at = data.get("updated_at", "")
        sess.applied_at = data.get("applied_at")
        sess.changes = {
            k: Change(**v) for k, v in (data.get("changes") or {}).items()
        }
        sess.tombstones = set(data.get("tombstones") or [])
        return sess

    @classmethod
    def list_sessions(cls, sandbox_base: Optional[Path | str] = None,
                      status: Optional[str] = None) -> List[Dict[str, Any]]:
        """扫描沙箱根目录，列出会话摘要（可按状态过滤，如只看 ready）。"""
        base = Path(sandbox_base or (app_dir() / "sandbox"))
        if not base.is_dir():
            return []
        out: List[Dict[str, Any]] = []
        for meta_path in sorted(base.glob(f"*/{META_NAME}")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if status and data.get("status") != status:
                continue
            out.append({
                "ticket_id": data.get("ticket_id"),
                "real_root": data.get("real_root", ""),
                "status": data.get("status"),
                "updated_at": data.get("updated_at", ""),
                "change_count": len(data.get("changes") or {}),
            })
        return out

    # ------------------------------------------------------------------ #
    # 路径映射
    # ------------------------------------------------------------------ #
    def _resolve_input(self, path: Path | str) -> Path:
        """把智能体给的路径解析为绝对路径：相对路径基于授权根。"""
        p = Path(path)
        if not p.is_absolute():
            if self.real_root is None:
                raise SandboxViolation(
                    f"未设置授权根，沙箱不接受相对路径：{path}")
            p = self.real_root / p
        return p.resolve()

    def _rel_key(self, p: Path) -> Optional[str]:
        """p 是否在授权根内；是则返回 posix 相对键，否则 None。"""
        if self.real_root is None:
            return None
        try:
            return p.relative_to(self.real_root).as_posix()
        except ValueError:
            return None

    def _assert_writable_state(self) -> None:
        if self.status not in (SandboxStatus.RUNNING, SandboxStatus.APPLY_FAILED):
            raise SandboxStateError(f"会话已{self.status}，不能再写入沙箱")

    def map_for_write(self, path: Path | str, *, side: bool = False
                      ) -> tuple[Path, Path, str, str]:
        """把一个**写入目标**映射到沙箱内虚拟路径。

        :param side: 调用方明确声明这是授权根外的辅助输出（智能体备份等）。
            根外非 side 写入一律拒绝（跨根绝对路径攻击面）。
        :return: (解析后的绝对真实路径, 虚拟路径, 变更键, 相对路径展示名)
        :raises SandboxViolation: 相对路径逃逸授权根，或非 side 跨根绝对路径。
        """
        self._assert_writable_state()
        raw = Path(path)
        p = self._resolve_input(raw)
        rel = self._rel_key(p)

        if rel is not None:
            virtual = self.overlay_dir.joinpath(*rel.split("/"))
            return p, virtual, rel, rel

        # 根外：仅允许显式 side 辅助输出
        if not side:
            raise SandboxViolation(
                f"写入目标「{p}」位于授权根之外，沙箱已拒绝跨根写入"
                + (f"（授权根：{self.real_root}）" if self.real_root else "（未设置授权根）"))
        parts = _side_parts(p)
        key = "@side/" + "/".join(parts)
        virtual = self.side_dir.joinpath(*parts)
        return p, virtual, key, str(p)

    def map_for_read(self, path: Path | str) -> Path:
        """把一个**读取目标**映射到实际读取路径（overlay → 真实透传）。

        :raises FileNotFoundError: 文件在沙箱中被删除（tombstone）。
        """
        p = self._resolve_input(path)
        rel = self._rel_key(p)
        if rel is not None:
            if rel in self.tombstones:
                raise FileNotFoundError(f"文件已在沙箱中删除：{rel}")
            virtual = self.overlay_dir.joinpath(*rel.split("/"))
            if virtual.exists():
                return virtual
        return p

    def exists(self, path: Path | str) -> bool:
        """合并视图的 exists（tombstone 遮蔽，overlay 优先）。"""
        try:
            return self.map_for_read(path).exists()
        except FileNotFoundError:
            return False

    def is_dir(self, path: Path | str) -> bool:
        try:
            return self.map_for_read(path).is_dir()
        except FileNotFoundError:
            return False

    def list_dir(self, path: Path | str) -> List[Dict[str, Any]]:
        """列出目录的合并视图：真实条目 + overlay 新增条目 − 沙箱删除项。

        授权根外目录直接透传（只读调查位置，如存档/日志目录）。
        """
        p = self._resolve_input(path)
        rel = self._rel_key(p)
        if rel is None:
            return [
                {"name": c.name, "path": str(c),
                 "is_dir": c.is_dir(),
                 "size": c.stat().st_size if c.is_file() else None}
                for c in sorted(p.iterdir()) if c.exists()
            ]

        overlay_dir = self.overlay_dir.joinpath(*rel.split("/")) if rel != "." else self.overlay_dir
        real_entries = {c.name: c for c in p.iterdir()} if p.is_dir() else {}
        virt_entries = {c.name: c for c in overlay_dir.iterdir()} if overlay_dir.is_dir() else {}
        names = sorted(set(real_entries) | set(virt_entries))
        out: List[Dict[str, Any]] = []
        for name in names:
            child_key = name if rel == "." else f"{rel}/{name}"
            if child_key in self.tombstones:
                continue
            chosen = virt_entries.get(name) or real_entries[name]
            out.append({
                "name": name,
                "path": str(chosen),
                "is_dir": chosen.is_dir(),
                "size": chosen.stat().st_size if chosen.is_file() else None,
            })
        return out

    # ------------------------------------------------------------------ #
    # 变更记录
    # ------------------------------------------------------------------ #
    def note_write(self, real_path: Path | str, virtual_path: Path, key: str,
                   relpath: str, size: Optional[int], *, op: Optional[str] = None,
                   is_text: bool = True) -> Change:
        """记录一次写入（幂等合并：同 key 多次写只保留最终一条）。"""
        real = str(Path(real_path).resolve())
        # tombstone 复活：移出删除集合，按真实文件是否存在重新定性
        self.tombstones.discard(key)
        existing = self.changes.get(key)
        if existing is not None and existing.op == ChangeOp.DELETE:
            # 删除后又写回：op 按真实文件现状重新定性
            existing.op = op or (ChangeOp.MODIFY if Path(real).exists()
                                 else ChangeOp.CREATE)
        if existing is not None and existing.op == ChangeOp.SIDE_WRITE:
            # side 区重复写（备份时间戳不同则是新 key，不会走到这）
            existing.size = size
            existing.virtual_path = str(virtual_path)
            existing.touch()
        elif existing is not None:
            existing.virtual_path = str(virtual_path)
            existing.size = size
            existing.is_text = is_text
            existing.error = ""
            existing.touch()
        else:
            if op is None:
                op = ChangeOp.MODIFY if Path(real).exists() else ChangeOp.CREATE
            self.changes[key] = Change(
                key=key, relpath=relpath, real_path=real, op=op,
                virtual_path=str(virtual_path), size=size, is_text=is_text)
        self.save()
        return self.changes[key]

    def note_delete(self, real_path: Path | str, key: str, relpath: str) -> Optional[Change]:
        """记录一次删除。

        真实位置本不存在、且沙箱内曾新建的文件 → 净零变更（清记录、删 overlay）；
        真实文件存在 → tombstone + delete 变更。
        """
        real = Path(real_path).resolve()
        virtual = self.overlay_dir.joinpath(*key.split("/")) if not key.startswith("@side/") else None

        if not real.exists():
            # 新建后又删 = 什么都没发生
            self.changes.pop(key, None)
            self.tombstones.discard(key)
            if virtual is not None and virtual.exists():
                virtual.unlink()
            self.save()
            return None

        self.tombstones.add(key)
        existing = self.changes.get(key)
        if existing is not None:
            existing.op = ChangeOp.DELETE
            existing.virtual_path = ""
            existing.size = None
            existing.touch()
            change = existing
        else:
            change = Change(key=key, relpath=relpath, real_path=str(real),
                            op=ChangeOp.DELETE, is_text=False)
            self.changes[key] = change
        # overlay 中的修改稿已无意义（apply 只看 tombstone）
        if virtual is not None and virtual.exists():
            virtual.unlink()
        self.save()
        return change

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def seal(self) -> None:
        """任务全部执行完毕，进入待审核状态。"""
        if self.status != SandboxStatus.RUNNING:
            raise SandboxStateError(f"会话状态为 {self.status}，不能封存")
        self.status = SandboxStatus.READY
        self.save()

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    def describe(self) -> Dict[str, Any]:
        """供 API/UI 使用的会话描述。"""
        return {
            "ticket_id": self.ticket_id,
            "real_root": str(self.real_root) or "",
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "applied_at": self.applied_at,
            "changes": [self._change_public(c) for c in self.changes.values()],
        }

    @staticmethod
    def _change_public(c: Change) -> Dict[str, Any]:
        return {
            "key": c.key,
            "relpath": c.relpath,
            "real_path": c.real_path,
            "op": c.op,
            "virtual_path": c.virtual_path,
            "size": c.size,
            "is_text": c.is_text,
            "backup_path": c.backup_path,
            "error": c.error,
            "updated_at": c.updated_at,
        }

    def change_content(self, key: str, *, limit: int = 200_000) -> Dict[str, Any]:
        """读取变更内容供审核预览（文本返回 new/old 文本，超限截断）。"""
        c = self.changes[key]
        result: Dict[str, Any] = self._change_public(c)
        if c.op == ChangeOp.DELETE:
            old = Path(c.real_path)
            result["old_text"] = _try_text(old) if old.is_file() else None
            result["new_text"] = None
            return result
        virtual = Path(c.virtual_path) if c.virtual_path else None
        if virtual and virtual.is_file():
            new_text = _try_text(virtual)
            if new_text is not None:
                result["new_text"] = new_text[:limit]
                result["truncated"] = len(new_text) > limit
            else:
                result["new_text"] = None
            if c.op in (ChangeOp.MODIFY, ChangeOp.DOWNLOAD):
                old = Path(c.real_path)
                result["old_text"] = _try_text(old) if old.is_file() else None
        return result

    # ------------------------------------------------------------------ #
    # 落盘 / 丢弃
    # ------------------------------------------------------------------ #
    def apply_changes(self) -> List[Dict[str, Any]]:
        """把全部变更应用到真实文件系统。

        顺序：先统一备份将被破坏的真实文件 → 再逐项应用。任一项失败不中断
        其余项；全部成功置 applied（清理 overlay/side），否则保持 ready 可重试。
        """
        if self.status not in (SandboxStatus.READY, SandboxStatus.APPLY_FAILED):
            raise SandboxStateError(f"会话状态为 {self.status}，仅 ready 会话可应用")

        results: List[Dict[str, Any]] = []
        failures = 0

        # 第一步：备份所有"将触碰真实位置"的现有文件（side_write 不备份）
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = app_dir() / "backups" / f"apply_{self.ticket_id}" / stamp
        manifest: Dict[str, str] = {}
        for c in self.changes.values():
            if c.op in (ChangeOp.SIDE_WRITE, ChangeOp.CREATE, ChangeOp.MKDIR):
                continue
            real = Path(c.real_path)
            if not real.exists():
                continue
            idx = len(manifest)
            dst = backup_root / f"{idx:03d}_{real.name}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                if real.is_dir():
                    shutil.copytree(real, dst)
                else:
                    shutil.copy2(real, dst)
                c.backup_path = str(dst)
                manifest[c.real_path] = str(dst)
            except OSError as exc:
                c.error = f"备份失败：{exc}"

        if manifest:
            (backup_root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # 第二步：逐项应用
        for c in self.changes.values():
            item = {"key": c.key, "relpath": c.relpath, "op": c.op,
                    "real_path": c.real_path, "backup_path": c.backup_path,
                    "ok": False, "error": ""}
            try:
                if c.op in (ChangeOp.CREATE, ChangeOp.MODIFY, ChangeOp.DOWNLOAD):
                    virtual = Path(c.virtual_path)
                    if not virtual.is_file():
                        raise FileNotFoundError(f"沙箱产物缺失：{virtual}")
                    real = Path(c.real_path)
                    real.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(virtual, real)
                elif c.op == ChangeOp.DELETE:
                    real = Path(c.real_path)
                    if real.is_dir():
                        shutil.rmtree(real)
                    elif real.exists():
                        real.unlink()
                elif c.op == ChangeOp.MKDIR:
                    Path(c.real_path).mkdir(parents=True, exist_ok=True)
                elif c.op == ChangeOp.SIDE_WRITE:
                    virtual = Path(c.virtual_path)
                    real = Path(c.real_path)
                    real.parent.mkdir(parents=True, exist_ok=True)
                    if virtual.is_dir():
                        shutil.copytree(virtual, real, dirs_exist_ok=True)
                    else:
                        shutil.copy2(virtual, real)
                item["ok"] = True
                c.error = ""
            except OSError as exc:
                failures += 1
                item["ok"] = False
                item["error"] = str(exc)
                c.error = str(exc)
            results.append(item)

        self.applied_at = datetime.now().isoformat()
        if failures:
            self.status = SandboxStatus.APPLY_FAILED
            self.save()
            return results

        # 全部成功：清产物、留 meta 审计
        self.status = SandboxStatus.APPLIED
        self.save()
        shutil.rmtree(self.overlay_dir, ignore_errors=True)
        shutil.rmtree(self.side_dir, ignore_errors=True)
        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        self.side_dir.mkdir(parents=True, exist_ok=True)
        return results

    def discard(self) -> None:
        """丢弃全部变更：删除沙箱目录，真实文件系统零变化。"""
        if self.status not in (SandboxStatus.READY, SandboxStatus.APPLY_FAILED,
                               SandboxStatus.RUNNING):
            raise SandboxStateError(f"会话状态为 {self.status}，无需丢弃")
        shutil.rmtree(self.root, ignore_errors=False)


# --------------------------------------------------------------------- #
# 运行中会话内存注册表（HTTP 端点操作内存实例；进程重启后退回磁盘 load）
# --------------------------------------------------------------------- #
_ACTIVE_SESSIONS: Dict[str, "SandboxSession"] = {}


def register_session(session: "SandboxSession") -> None:
    """登记一个由本轮对话创建的运行中/待审会话。"""
    _ACTIVE_SESSIONS[session.ticket_id] = session


def get_registered_session(ticket_id: str) -> Optional["SandboxSession"]:
    """取内存中的会话实例；不存在返回 None（调用方可再试磁盘 load）。"""
    return _ACTIVE_SESSIONS.get(ticket_id)


def registered_sessions(status: Optional[str] = None) -> List["SandboxSession"]:
    """列出内存登记的会话，可按状态过滤。"""
    return [s for s in _ACTIVE_SESSIONS.values()
            if status is None or s.status == status]


# --------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------- #
_TEXT_SUFFIXES = {
    ".lua", ".py", ".js", ".ts", ".json", ".ini", ".cfg", ".conf", ".xml",
    ".toml", ".yaml", ".yml", ".cs", ".c", ".cpp", ".h", ".java", ".kt",
    ".sh", ".bat", ".ps1", ".sql", ".txt", ".md", ".log", ".csv",
}


def _try_text(path: Path, *, limit_bytes: int = 2_000_000) -> Optional[str]:
    """尝试把文件按文本解码（用于审核预览）；非文本/超限返回 None。"""
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > limit_bytes:
        return None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None
