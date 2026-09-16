"""制度化协作治理层（参照「三省六部」运行架构）。

把多智能体协作从"编排器一把梭"升级为**分权制衡的状态机**：

```text
接待官 Intake     接待官 Intake    意图分拣 / 闲聊直答 / 建票
      │
      ▼
规划官 Planning   规划官           需求 → 任务 DAG（LLM 动态规划，模板兜底）
      │
      ▼
审议官 Review     审议官           可行性 / 安全性强制审议，可封驳（≤3 轮）
      │  ┌──────────┘
      │  封驳（附问题清单，回规划官修订）
      ▼ ▼
调度官 Assigned   调度官           权限校验后派发给领域智能体（现有编排器）
      │
      ▼
执行   Doing      领域智能体集群   阶段并行 / 任务级门控
      │
      ▼
验收   Verification               必选任务成败核定
      │
      ▼
完成   Done                       回奏（自然语言收口）
```

与三省六部的对应：太子=接待官、中书省=规划官、门下省=审议官、
尚书省=调度官（``GameAgentOrchestrator``）、六部=15 个领域智能体。

本包为**内存态核心制度链路**：任务票（TaskTicket）与流转日志仅存活于
单次请求；持久化 / 停滞调度 / 人工看板为后续阶段。
"""

from __future__ import annotations

from .coordinator import GovernanceCoordinator
from .permissions import Role, can_dispatch
from .state_machine import FlowEntry, GovState, TaskTicket, TransitionError

__all__ = [
    "FlowEntry",
    "GovState",
    "GovernanceCoordinator",
    "Role",
    "TaskTicket",
    "TransitionError",
    "can_dispatch",
]
