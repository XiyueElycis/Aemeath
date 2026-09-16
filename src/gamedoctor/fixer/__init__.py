"""⑤ 自动修复核心（PRD §4.5 F5，本版核心）。

- :mod:`primitives` —— 修复原语库（文件 / 配置 / 平台 / 运行时）
- :mod:`policy` —— 分级授权（L0-L3 白名单/拦截）
- :mod:`executor` —— 事务化执行器（备份 / 执行 / 验证 / 回滚）
- :mod:`backup` —— 备份与回滚管理
"""

# 导入各子模块；其中 primitives 的导入会触发所有内置原语的注册（副作用）
from .backup import BackupManager
from .executor import ExecutionMode, TransactionalExecutor
from .policy import AuthorizationPolicy
from .primitives import REGISTRY, RepairPrimitive

__all__ = [
    "BackupManager",
    "ExecutionMode",
    "TransactionalExecutor",
    "AuthorizationPolicy",
    "RepairPrimitive",
    "REGISTRY",
]
