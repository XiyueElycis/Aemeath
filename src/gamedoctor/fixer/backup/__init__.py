"""备份与回滚管理（PRD §4.5.3 事务化执行闭环）。

强制备份：任何写操作前必备份，且备份可一键回滚。
备份目录：``~/.gamedoctor/backups/<诊断ID>/``
"""

# 统一在此重导出，对外只暴露这两个符号，隐藏内部实现模块
from .manager import BackupManager, BackupRecord

__all__ = ["BackupManager", "BackupRecord"]
