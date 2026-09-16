"""访问模式子系统：沙箱授权执行与任务级审批门。

- :class:`SandboxSession`（``session.py``）：票据级副本沙箱，读透传真实目录、
  写重定向到 ``~/.gamedoctor/sandbox/<ticket_id>/`` 覆盖层，变更经用户审核后
  才落盘（apply）或丢弃（discard）。
- :mod:`gamedoctor.sandbox.io`：session 可选的统一文件原语——session 为 None
  时等价直接落盘（直接访问模式），非 None 时全部重定向进沙箱。
- :class:`ApprovalGate`（``gate.py``）：任务执行前的用户授权门（asyncio
  事件挂起 + 超时自动拒绝）。
- :mod:`gamedoctor.sandbox.context`：贯穿编排链路的访问上下文（contextvar）。
"""

from __future__ import annotations

from .gate import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
)
from .session import (
    Change,
    SandboxSession,
    SandboxStatus,
    SandboxViolation,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalRequest",
    "Change",
    "SandboxSession",
    "SandboxStatus",
    "SandboxViolation",
]
