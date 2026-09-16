"""分级授权（PRD §4.5.2 动作分级授权，安全核心）。

L0 只读 / L1 安全自动 / L2 需确认 / L3 禁止自动。
"""

# 重导出策略协议、便捷判定函数与全局默认策略
from .levels import AuthorizationPolicy, classify, default_policy

__all__ = ["AuthorizationPolicy", "classify", "default_policy"]
