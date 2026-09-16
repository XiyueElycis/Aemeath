"""平台级修复原语（PRD §4.5.1 平台级）。

Steam 校验完整性、Epic repair、清云存档冲突。由平台适配层选择对应命令。
"""

from __future__ import annotations

from pathlib import Path

from ...models import ActionLevel, ActionResult, FixStatus, TechStackFingerprint
from .base import RepairPrimitive, register


@register
class SteamVerifyIntegrityPrimitive(RepairPrimitive):
    """Steam 校验游戏文件完整性（steamcmd validate）。"""

    name = "steam_verify_integrity"
    category = "platform"
    default_level = ActionLevel.L1_SAFE
    summary = "Steam 校验完整性"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        # 校验完整性本身不改本地文件，声明空列表即可
        return []

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        # TODO: 调用 steamcmd +app_update <app_id> validate
        return ActionResult(status=FixStatus.FAILED, message="steam_verify_integrity 未实现")

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        return False


@register
class EpicRepairPrimitive(RepairPrimitive):
    """Epic Games 修复安装。"""

    name = "epic_repair"
    category = "platform"
    default_level = ActionLevel.L1_SAFE
    summary = "Epic 修复安装"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        return []  # Epic 修复走官方客户端，不改本地游戏文件

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        # TODO: 调用 Epic CLI 修复
        return ActionResult(status=FixStatus.FAILED, message="epic_repair 未实现")

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        return False  # 命令落地前恒未通过
