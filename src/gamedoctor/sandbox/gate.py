"""任务级用户授权门（沙箱模式每个任务执行前须显式批准）。

机制：纯内存 + :class:`asyncio.Event`——任务执行协程在 :meth:`ApprovalGate.request`
上挂起等待，桌面端轮询 ``GET /approvals/pending`` 展示请求，用户操作后经
``POST /approvals/{id}`` 调 :meth:`decide` 唤醒；不做忙等轮询。

超时：TTL（默认 600 秒）未决自动按拒绝处理，避免任务永久挂起；用户决定与
超时在边界上同时发生时，以已到达的用户决定优先（wait_for 醒来后二次确认）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_APPROVAL_TTL = 600.0  # 10 分钟


@dataclass
class ApprovalRequest:
    """一条待决授权请求（对用户可读、可序列化为 JSON）。"""

    ticket_id: str
    agent: str
    operation: str = ""        # agent data.operation（edit/read/list/download…）
    target: str = ""           # file / game_dir / url
    kind: str = "read"         # read/write/download/install
    summary: str = ""          # 任务一句话说明
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "agent": self.agent,
            "operation": self.operation,
            "target": self.target,
            "kind": self.kind,
            "summary": self.summary,
            "created_at": self.created_at,
        }


@dataclass
class ApprovalDecision:
    """授权结果（拒绝/超时不抛异常，由编排层转为跳过结果）。"""

    approved: bool
    request_id: str = ""
    reason: str = ""
    timeout: bool = False


class _Pending:
    """内部：一条挂起中的请求及其唤醒事件。"""

    def __init__(self, req: ApprovalRequest, ttl: float):
        self.req = req
        self.event = asyncio.Event()
        self.ttl = ttl
        self.decision: Optional[ApprovalDecision] = None


class ApprovalGate:
    """进程内待决授权登记表（桌面单用户场景，不做持久化）。"""

    def __init__(self, ttl: float = DEFAULT_APPROVAL_TTL):
        self._ttl = ttl
        self._pending: Dict[str, _Pending] = {}
        # 已决请求留痕（进程生命周期内量很小）：供 API 区分 404（从未存在）
        # 与 409（重复裁决）
        self._decided: Dict[str, ApprovalDecision] = {}

    async def request(self, req: ApprovalRequest,
                      ttl: Optional[float] = None) -> ApprovalDecision:
        """登记请求并挂起等待用户决定；超时返回拒绝决定。"""
        pend = _Pending(req, ttl if ttl is not None else self._ttl)
        self._pending[req.id] = pend
        try:
            await asyncio.wait_for(pend.event.wait(), timeout=pend.ttl)
        except asyncio.TimeoutError:
            # 边界竞态：用户决定与超时同时到达时尊重用户决定
            if pend.decision is not None:
                self._pending.pop(req.id, None)
                self._decided[req.id] = pend.decision
                return pend.decision
            pend.decision = ApprovalDecision(
                approved=False, request_id=req.id,
                reason="授权超时（等待超过 %d 秒，已自动拒绝）" % int(pend.ttl),
                timeout=True)
        self._pending.pop(req.id, None)
        self._decided.setdefault(req.id, pend.decision)
        return pend.decision

    def is_decided(self, request_id: str) -> bool:
        """该请求是否已被裁决（或已超时）。"""
        return request_id in self._decided

    def list_pending(self, ticket_id: Optional[str] = None) -> List[ApprovalRequest]:
        """列出待决请求（可按票据过滤），按登记时间排序。"""
        items = [p.req for p in self._pending.values()
                 if p.decision is None
                 and (ticket_id is None or p.req.ticket_id == ticket_id)]
        return sorted(items, key=lambda r: r.created_at)

    def decide(self, request_id: str, approved: bool,
               reason: str = "") -> bool:
        """写入用户决定并唤醒等待协程。

        :return: True=已受理；False=id 未知或已决（调用方据此回 404/409）。
        """
        pend = self._pending.get(request_id)
        if pend is None or pend.decision is not None:
            return False
        decision = ApprovalDecision(
            approved=approved, request_id=request_id,
            reason=reason or ("用户批准" if approved else "用户拒绝授权"))
        pend.decision = decision
        # 即时留痕：保证并发重复裁决立刻可见 409，不等等待协程恢复
        self._decided[request_id] = decision
        pend.event.set()
        return True


# --------------------------------------------------------------------- #
# 单例
# --------------------------------------------------------------------- #
_gate: Optional[ApprovalGate] = None


def get_gate() -> ApprovalGate:
    """获取进程级授权门单例（FastAPI 与编排层共用）。"""
    global _gate
    if _gate is None:
        _gate = ApprovalGate()
    return _gate


def reset_gate() -> None:
    """清空单例（测试隔离用）。"""
    global _gate
    _gate = None
