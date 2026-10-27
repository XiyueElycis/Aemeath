"""编程智能体 · 检查点（写入前的文件快照）。

审批只能挡住「不该做的改动」，挡不住「批准了但做错了」。所以每次改写前先把
受影响文件的原样存一份，模型或用户都能用 ``undo_change`` 退回去。

存放位置按工作区根目录的哈希分桶，放在应用数据目录下（``AEMEATH_AGENT_DATA_HOME``
可覆盖），**不往用户仓库里塞任何文件**。每个批次最多保留 :data:`MAX_BATCHES` 份。

快照粒度是「一批受影响的文件」，因为一次工具调用可能同时动多个路径（删除目录、
移动文件），只存单个文件会留下半还原的中间态。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# 每个工作区保留多少批快照（够回溯最近的折腾，又不会无限长）
MAX_BATCHES = 20


def _data_home() -> Path:
    base = os.environ.get("AEMEATH_AGENT_DATA_HOME") or os.path.expanduser(
        "~/.aemeath_agent")
    return Path(base)


def checkpoints_dir(root: Path, directory: Optional[Path | str] = None) -> Path:
    """某工作区的快照目录（按根路径哈希分桶，避免多项目互相覆盖）。"""
    if directory is not None:
        return Path(directory)
    key = hashlib.md5(str(root).lower().encode("utf-8")).hexdigest()[:12]
    return _data_home() / "coding" / "checkpoints" / key


@dataclass
class SnapshotEntry:
    rel: str
    existed: bool        # 快照时文件是否存在（新建文件的撤销 = 删掉它）


class CheckpointStore:
    """一个工作区的检查点仓库。"""

    def __init__(self, root: Path, directory: Optional[Path | str] = None) -> None:
        self.root = Path(root).resolve()
        self.dir = checkpoints_dir(self.root, directory)

    # ------------------------------------------------------------------ #
    def snapshot(self, paths: List[Path], *, always: bool = False
                 ) -> Optional[str]:
        """把一组绝对路径存成一批快照，返回批次 id。

        只存**文件**；目录会展开成目录下的所有文件，这样删除目录也能整体还原。
        ``always=True`` 时即使没有可存文件也建一个空批次——新建文件、复制到
        新路径这类操作需要一个「批次」来登记「撤销时该删掉谁」。
        """
        files: List[Path] = []
        for path in paths:
            path = Path(path)
            if path.is_dir():
                files.extend(p for p in path.rglob("*") if p.is_file())
            elif path.is_file():
                files.append(path)
        if not files and not always:
            return None

        batch = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        target = self.dir / batch
        target.mkdir(parents=True, exist_ok=True)
        entries: List[Dict[str, Any]] = []
        for idx, file in enumerate(files):
            try:
                rel = file.relative_to(self.root).as_posix()
            except ValueError:
                continue
            blob = f"{idx:04d}.bin"
            try:
                shutil.copy2(file, target / blob)
            except OSError:
                continue
            entries.append({"rel": rel, "blob": blob, "existed": True})

        manifest = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": entries}
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._trim()
        return batch

    def note_new(self, batch: Optional[str], rel: str) -> None:
        """登记「这个文件在快照之后才被创建」，撤销时应当删除它。"""
        if not batch:
            return
        path = self.dir / batch / "manifest.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        data.setdefault("files", []).append(
            {"rel": rel, "blob": "", "existed": False})
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # ------------------------------------------------------------------ #
    def list(self) -> List[Dict[str, Any]]:
        """列出快照批次（最新在前）。"""
        rows: List[Dict[str, Any]] = []
        if not self.dir.exists():
            return rows
        for item in sorted(self.dir.iterdir(), reverse=True):
            manifest = item / "manifest.json"
            if not manifest.exists():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append({
                "batch": item.name,
                "at": data.get("at", ""),
                "files": [e.get("rel", "") for e in data.get("files", [])],
            })
        return rows

    def restore(self, batch: str, files: Optional[List[str]] = None) -> str:
        """把一个批次（或其中指定文件）还原到快照时刻的状态。"""
        target = self.dir / batch
        manifest = target / "manifest.json"
        if not manifest.exists():
            raise FileNotFoundError(f"找不到快照批次：{batch}")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        wanted = set(files or [])
        restored: List[str] = []
        for entry in data.get("files", []):
            rel = str(entry.get("rel") or "")
            if not rel or (wanted and rel not in wanted):
                continue
            dest = self.root / rel
            if not entry.get("existed", True):
                if dest.exists():
                    dest.unlink()
                    restored.append(f"删除 {rel}")
                continue
            blob = target / str(entry.get("blob") or "")
            if not blob.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(blob, dest)
            restored.append(f"还原 {rel}")
        return "、".join(restored) if restored else "（这批快照里没有可还原的文件）"

    def restore_latest(self, rel: str = "") -> str:
        """还原某个文件最近一次快照；不给路径则还原最近一批。"""
        batches = self.list()
        if not batches:
            raise FileNotFoundError("还没有任何快照")
        if not rel:
            return self.restore(batches[0]["batch"])
        for row in batches:                      # 最新在前，找到第一个含该文件的批次
            if rel in row["files"]:
                return self.restore(row["batch"], files=[rel])
        raise FileNotFoundError(f"没有「{rel}」的历史快照")

    # ------------------------------------------------------------------ #
    def _trim(self) -> None:
        if not self.dir.exists():
            return
        batches = sorted(p.name for p in self.dir.iterdir() if p.is_dir())
        for old in batches[:-MAX_BATCHES]:
            shutil.rmtree(self.dir / old, ignore_errors=True)


__all__ = ["CheckpointStore", "checkpoints_dir", "MAX_BATCHES"]
