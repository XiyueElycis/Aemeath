"""备份与回滚管理（PRD §4.5.3，强制备份 + 一键回滚）。

实现思路：
- 备份时把将被改动的文件快照复制到 ``<backup_root>/<诊断ID>/<动作ID>/``，
  并写 ``manifest.json`` 记录「目标路径 → 备份相对路径」映射。
- 回滚时按 manifest 反向复制还原；若目标原本不存在，则删除被新建的文件。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ...config import app_dir
from ...errors import BackupError

MANIFEST_NAME = "manifest.json"


@dataclass
class BackupRecord:
    """一次动作的备份记录。"""

    diagnosis_id: str
    action_id: str
    backup_dir: Path                       # <root>/<诊断ID>/<动作ID>
    entries: dict[str, str] = field(default_factory=dict)  # 目标路径 -> 备份相对路径
    created_at: datetime = field(default_factory=datetime.now)


class BackupManager:
    """备份与回滚管理器。可独立使用，也可由执行器调用。"""

    def __init__(self, backup_root: Path | None = None):
        self.backup_root = backup_root or (app_dir() / "backups")

    def _action_dir(self, diagnosis_id: str, action_id: str) -> Path:
        return self.backup_root / diagnosis_id / action_id

    def backup(self, diagnosis_id: str, action_id: str, paths: list[Path]) -> BackupRecord:
        """把 ``paths`` 中的现有文件/目录快照到备份目录，返回备份记录。

        每个目标（文件或目录）复制到 ``<root>/<诊断ID>/<动作ID>/`` 下的独立子
        目录，避免同名冲突；同时写 manifest.json 记录「原始路径 → 备份路径」映射，
        供回滚时反向还原。不存在的路径跳过（无内容可备份）。
        """
        action_dir = self._action_dir(diagnosis_id, action_id)
        action_dir.mkdir(parents=True, exist_ok=True)
        record = BackupRecord(diagnosis_id, action_id, action_dir)

        for i, src in enumerate(paths):
            if not src.exists():
                continue
            if src.is_dir():
                # 目录：整体递归复制（copytree）
                dst = action_dir / f"d{i}" / src.name
                shutil.copytree(src, dst)
                record.entries[str(src)] = str(dst.relative_to(action_dir))
            else:
                # 文件：copy2 保留元数据（时间戳等）
                dst = action_dir / f"f{i}" / src.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                record.entries[str(src)] = str(dst.relative_to(action_dir))

        self._write_manifest(record)
        return record

    def _write_manifest(self, record: BackupRecord) -> None:
        manifest = {
            "diagnosis_id": record.diagnosis_id,
            "action_id": record.action_id,
            "created_at": record.created_at.isoformat(),
            "entries": record.entries,
        }
        (record.backup_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def rollback(self, record: BackupRecord) -> None:
        """按备份记录把文件/目录还原到原始位置。

        反向操作：对每个 ``目标路径 → 备份相对路径`` 映射，把备份内容复制回
        目标位置；目录先删后拷（保证彻底还原，而非合并残留）。
        """
        manifest_path = record.backup_dir / MANIFEST_NAME
        if not manifest_path.exists():
            raise BackupError(f"备份清单缺失，无法回滚: {manifest_path}")

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for target, rel in data.get("entries", {}).items():
            src = record.backup_dir / rel
            dst = Path(target)
            if not src.exists():
                raise BackupError(f"备份文件缺失: {src}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)  # 先删旧目标，确保与新文件状态一致
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    def list_records(self, diagnosis_id: str) -> list[BackupRecord]:
        """列出某诊断 ID 下的所有备份记录（无则返回空列表）。"""
        diag_dir = self.backup_root / diagnosis_id
        if not diag_dir.is_dir():
            return []
        records: list[BackupRecord] = []
        # 按动作目录名排序输出，便于上层反向遍历实现「后改的先还原」
        for action_dir in sorted(diag_dir.iterdir()):
            manifest = action_dir / MANIFEST_NAME
            if not manifest.exists():
                continue
            data = json.loads(manifest.read_text(encoding="utf-8"))
            records.append(
                BackupRecord(
                    diagnosis_id=data["diagnosis_id"],
                    action_id=data["action_id"],
                    backup_dir=action_dir,
                    entries=data.get("entries", {}),
                    created_at=datetime.fromisoformat(data["created_at"]),
                )
            )
        return records
