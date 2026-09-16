"""任务状态机与任务票（三省六部「状态严格递进」契约）。

状态单向递进，唯二回路：
- ``Review → Planning``：审议官封驳，附问题清单退回规划官（最多 3 轮）；
- ``Intake → Done``：接待官判定为闲聊/问答，直接回奏，不建执行链路。

任何绕过状态机的跳转都抛 :class:`TransitionError`——智能体自身无权
改任务状态，这是「不可越级」制度的代码保证。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class GovState(str, Enum):
    """任务生命周期状态（值沿用三省六部英文状态名，便于对照）。"""

    INTAKE = "Intake"                # 接待官分拣
    PLANNING = "Planning"            # 规划官起草方案
    REVIEW = "Review"                # 审议官审议
    ASSIGNED = "Assigned"            # 调度官接单派发
    DOING = "Doing"                  # 领域智能体执行中
    VERIFICATION = "Verification"    # 执行结果验收
    DONE = "Done"                    # 终态：完成
    CANCELLED = "Cancelled"          # 终态：取消


# 合法状态转移表：状态机的唯一事实来源
ALLOWED_TRANSITIONS: Dict[GovState, frozenset[GovState]] = {
    GovState.INTAKE: frozenset({GovState.PLANNING, GovState.DONE, GovState.CANCELLED}),
    # Planning 回 Intake = 规划官封还：散点问答无需立项，退接待官直办
    GovState.PLANNING: frozenset({GovState.REVIEW, GovState.INTAKE, GovState.CANCELLED}),
    # Review 回 Planning = 封驳；进 Assigned = 准奏
    GovState.REVIEW: frozenset({GovState.PLANNING, GovState.ASSIGNED, GovState.CANCELLED}),
    GovState.ASSIGNED: frozenset({GovState.DOING, GovState.CANCELLED}),
    GovState.DOING: frozenset({GovState.VERIFICATION, GovState.CANCELLED}),
    GovState.VERIFICATION: frozenset({GovState.DONE, GovState.CANCELLED}),
    GovState.DONE: frozenset(),
    GovState.CANCELLED: frozenset(),
}

# 终态不可再改（对应三省六部「一旦 Done 无改」契约）
TERMINAL_STATES = frozenset({GovState.DONE, GovState.CANCELLED})

# 封驳最大轮次：第 3 轮仍只有软建议时强制通过；存在硬伤则终止交付
MAX_REVIEW_ROUNDS = 3

# 票号日内序号（内存态；持久化阶段由 DB 序列取代）
_ticket_seq = itertools.count(1)


class TransitionError(RuntimeError):
    """非法状态转移（越级 / 终态再改 / 跳步）。"""


@dataclass
class FlowEntry:
    """一条流转记录（对应三省六部 flow_log：谁→谁、备注、时间）。"""

    at: str
    from_role: str
    to_role: str
    remark: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "at": self.at,
            "from": self.from_role,
            "to": self.to_role,
            "remark": self.remark,
        }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_ticket_id() -> str:
    """生成票号：GD-YYYYMMDD-NNN（Game Doctor 缩写，对应三省六部 JJC 编号）。"""
    return f"GD-{datetime.now():%Y%m%d}-{next(_ticket_seq):03d}"  # noqa: DTZ005  # 票号取本地日历日


@dataclass
class TaskTicket:
    """一张任务票：贯穿接待→规划→审议→派发→执行→验收→回奏全链路。

    状态只能通过 :meth:`transition` 变更；每次转移自动写 flow_log，
    使整个协作过程可回放、可审计。
    """

    title: str
    mode: str = "edit"                       # edit / plan
    intent: Optional[str] = None             # detect_intent 命中的模板键
    ticket_id: str = field(default_factory=_new_ticket_id)
    state: GovState = GovState.INTAKE
    current_role: str = "接待官"
    review_round: int = 0
    plan: Any = None                         # OrchestrationPlan（动态/模板）
    plan_source: str = ""                    # template / llm / single
    review_verdicts: List[Any] = field(default_factory=list)
    flow_log: List[FlowEntry] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)          # 过程备注（同步进 thinking）
    outcome: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    forced_approved: bool = False            # 第 3 轮强制通过（仅软建议时允许）

    # ------------------------------------------------------------------ #
    def transition(self, to: GovState, from_role: str, to_role: str,
                   remark: str) -> FlowEntry:
        """执行状态转移并记录流转。

        :raises TransitionError: 目标状态不在合法转移表内（越级/跳步/终态再改）。
        """
        allowed = ALLOWED_TRANSITIONS.get(self.state, frozenset())
        if to not in allowed:
            raise TransitionError(
                f"非法状态转移：{self.state.value} → {to.value}"
                f"（允许：{', '.join(s.value for s in allowed) or '无（终态）'}）"
            )
        entry = FlowEntry(at=_now(), from_role=from_role, to_role=to_role, remark=remark)
        self.flow_log.append(entry)
        self.state = to
        self.current_role = to_role
        self.updated_at = entry.at
        if to == GovState.REVIEW:
            # 每进入一次审议即开启新一轮（封驳后重新进 Review 会继续累加）
            self.review_round += 1
        return entry

    def note(self, text: str) -> None:
        """追加一条过程备注（不改变状态，对应 progress_log）。"""
        self.notes.append(text)
        self.updated_at = _now()

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES
