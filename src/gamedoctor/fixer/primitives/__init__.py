"""修复原语库（PRD §4.5.1）。

修复动作按**操作类型**组织（文件 / 配置 / 平台 / 运行时），由技术栈指纹填参，
而非为每个游戏写死脚本。

导入本包即完成所有内置原语的注册，可通过 :data:`REGISTRY` 查询。
"""

# 导入各原语模块时，类上的 @register 装饰器即触发注册，填充 REGISTRY
from .base import REGISTRY, RepairPrimitive, get, register
from .config import AppendLaunchArgPrimitive, EditConfigPrimitive
from .file import CleanCachePrimitive, QuarantineFilePrimitive
from .platform import EpicRepairPrimitive, SteamVerifyIntegrityPrimitive
from .runtime import InstallRuntimePrimitive
from .text import ReplaceTextPrimitive

__all__ = [
    "REGISTRY",
    "AppendLaunchArgPrimitive",
    "CleanCachePrimitive",
    "EditConfigPrimitive",
    "EpicRepairPrimitive",
    "InstallRuntimePrimitive",
    "QuarantineFilePrimitive",
    "RepairPrimitive",
    "ReplaceTextPrimitive",
    "SteamVerifyIntegrityPrimitive",
    "get",
    "register",
]
