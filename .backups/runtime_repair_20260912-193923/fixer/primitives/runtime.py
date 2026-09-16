"""运行时级修复原语（PRD §4.5.1 运行时级）。

安装/修复 VC++、.NET、DirectX 运行库。缺失组件由运行时探测给出。
多数为 L2（安装软件需确认）。
"""

from __future__ import annotations

from pathlib import Path

from ...models import ActionLevel, ActionResult, FixStatus, TechStackFingerprint
from .base import RepairPrimitive, register


@register
class InstallRuntimePrimitive(RepairPrimitive):
    """安装/修复运行时组件（VC++ / .NET / DirectX）。"""

    name = "install_runtime"
    category = "runtime"
    default_level = ActionLevel.L2_CONFIRM
    summary = "安装/修复运行时组件"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        return []  # 安装器自身管理，不改游戏文件

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        component = params.get("component", "")
        # TODO: 定位并静默安装对应运行时（vc_redist.x64.exe / dotnet-install / dxwebsetup）
        return ActionResult(status=FixStatus.FAILED, message=f"install_runtime 未实现: {component}")

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        return False  # TODO: 探测组件是否就绪（注册表/文件版本）后再判定
