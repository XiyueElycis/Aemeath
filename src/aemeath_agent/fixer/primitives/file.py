"""文件级修复原语（PRD §4.5.1 文件级）。

移除/隔离冲突 Mod、清理缓存、修复缺失资源。路径由指纹识别给出。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ...models import ActionLevel, ActionResult, FixStatus, TechStackFingerprint
from .base import RepairPrimitive, register


@register
class QuarantineFilePrimitive(RepairPrimitive):
    """隔离冲突 Mod：把目标文件/目录移入隔离目录（而非删除，天然可回滚）。"""

    name = "quarantine_file"
    category = "file"
    default_level = ActionLevel.L1_SAFE
    summary = "隔离冲突 Mod / 文件"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        path = Path(params.get("path", ""))
        return [path] if path else []

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        src = Path(params.get("path", ""))
        if not src.exists():
            return ActionResult(status=FixStatus.FAILED, message=f"路径不存在: {src}")
        # 隔离而非删除：改名为 .quarantine，等于"软删除"，天然可逆（改名可还原）
        quarantine = src.parent / f"{src.name}.quarantine"
        # TODO: 已存在隔离目录时做去重/覆盖策略
        shutil.move(str(src), str(quarantine))
        return ActionResult(status=FixStatus.SUCCESS, message=f"已隔离: {src}")

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        # 检查点：目标路径已不存在即视为隔离成功
        src = Path(params.get("path", ""))
        return not src.exists()


@register
class CleanCachePrimitive(RepairPrimitive):
    """清理缓存目录（先重命名为 .bak，验证通过后再删除）。"""

    name = "clean_cache"
    category = "file"
    default_level = ActionLevel.L1_SAFE
    summary = "清理缓存目录"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        path = Path(params.get("path", ""))
        return [path] if path else []

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        path = Path(params.get("path", ""))
        if not path.exists():
            return ActionResult(status=FixStatus.SUCCESS, message=f"缓存已不存在: {path}")
        # 重命名为 .bak 的「软删除」：必要时可手动恢复，符合可回滚原则
        bak = path.parent / f"{path.name}.bak"
        shutil.move(str(path), str(bak))
        return ActionResult(status=FixStatus.SUCCESS, message=f"已清理缓存: {path}")

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        path = Path(params.get("path", ""))
        return not path.exists()  # 检查点：原路径消失即视为清理成功
