"""事务化执行器（PRD §4.5.3 事务化执行闭环）。

备份 → 执行 → 验证 → 回滚。支持 dry-run 与单步模式。
"""

# 统一重导出执行器对外符号，隐藏内部实现细节
from .transactional import ExecutionMode, TransactionalExecutor

__all__ = ["ExecutionMode", "TransactionalExecutor"]
