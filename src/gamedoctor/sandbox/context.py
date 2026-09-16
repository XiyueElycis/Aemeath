"""访问模式上下文（contextvar）与任务授权分类。

沙箱授权模式下，协调器在一轮对话开始时建立 :class:`AccessContext` 并经
:func:`use_access`（或同步 :func:`bind_access`）写入 contextvar；编排器/多智能体
系统在**唯一执行收口** ``MultiAgentSystem.execute_task`` 处读取上下文，完成三件事：

1. 把当前沙箱会话注入 ``task.data["_sandbox_session"]``（智能体落盘自动重定向）；
2. 每个任务执行前向授权门登记 :class:`ApprovalRequest` 并挂起等待用户裁决；
3. 拒绝/超时统一转成 ``AgentResult(success=False, skipped=True)``，状态机不生造转移。

contextvar 的复制语义保证：协调器协程里 set 之后，``asyncio.gather`` 派生的并行
任务各自拷贝到同一份上下文，无需在调用链上层层传参。
"""

from __future__ import annotations

import contextvars
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..governance.permissions import MUTATING_AGENTS
from .gate import ApprovalDecision, ApprovalGate, ApprovalRequest
from .session import SandboxSession

ACCESS_DIRECT = "direct"
ACCESS_SANDBOX = "sandbox"
ACCESS_MODES = (ACCESS_DIRECT, ACCESS_SANDBOX)

# 操作名 → 性质判定：script_editor 的 read/list 虽出自"写智能体"，但只读
_READ_OPS = frozenset({"read", "list", "query", "check", "scan", "status", ""})

# game_content（版本/存档/DLC 合并体）的只读子项：version 查询与当前 dlc 扫描
# 均不落盘；saves 备份/迁移与 content 全做（含备份）按写入对待。
_CONTENT_READ_OPS = frozenset({
    "version", "update", "updates", "update_management", "version_check",
    "dlc", "mod", "mods", "dlc_management",
})


_ACCESS_VAR: contextvars.ContextVar[Optional["AccessContext"]] = contextvars.ContextVar(
    "gamedoctor_access_context", default=None)


@dataclass
class AccessContext:
    """一轮对话的访问模式上下文。

    :param access_mode: ``direct``（直接访问，维持现状）或 ``sandbox``（沙箱授权）。
    :param ticket_id: 任务票号，授权请求与沙箱会话都挂在它下面。
    :param session: 沙箱会话；direct 模式为 None。
    :param gate: 授权门（默认进程单例，测试可注入）。
    """

    access_mode: str
    ticket_id: str
    game_name: str = ""
    game_dir: str = ""
    session: Optional[SandboxSession] = None
    gate: Optional[ApprovalGate] = None

    def __post_init__(self) -> None:
        if self.access_mode not in ACCESS_MODES:
            raise ValueError(f"未知访问模式：{self.access_mode}（可选：{ACCESS_MODES}）")
        if self.gate is None:
            from .gate import get_gate
            self.gate = get_gate()

    @property
    def is_sandbox(self) -> bool:
        return self.access_mode == ACCESS_SANDBOX

    @staticmethod
    def classify(agent: str, data: Optional[Dict[str, Any]]) -> str:
        """判定任务性质：read（只读）/ write（写入）/ download（下载）/ install（安装）。"""
        data = data or {}
        op = str(data.get("operation") or "").strip().lower()
        if agent == "web_search" and (op == "download" or data.get("url")):
            return "download"
        if agent == "installation":
            return "install"
        if agent in MUTATING_AGENTS:
            if agent == "script_editor" and op in _READ_OPS:
                return "read"
            if agent == "game_content":
                # 合并体按子项 op 细分（data["op"] 为工作流分流字段，
                # data["operation"] 兼容 LLM 直办命名）；未知/缺省保守判写
                sub = str(data.get("op") or data.get("operation") or "").strip().lower()
                return "read" if sub in _CONTENT_READ_OPS else "write"
            return "write"
        return "read"

    @staticmethod
    def target_of(agent: str, data: Optional[Dict[str, Any]]) -> str:
        """提取授权弹窗要展示的目标（文件 / URL / 游戏目录）。"""
        data = data or {}
        for key in ("file", "url", "save_path", "game_dir"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        return "（未指定具体目标）"

    async def authorize(self, agent: str, data: Optional[Dict[str, Any]]) -> ApprovalDecision:
        """为一个任务登记授权请求并等待用户裁决。"""
        data = data or {}
        kind = self.classify(agent, data)
        kind_label = {"read": "只读", "write": "写入",
                      "download": "下载", "install": "安装"}[kind]
        op = str(data.get("operation") or "").strip()
        summary = (
            f"智能体 {agent} 请求{kind_label}权限"
            + (f"（操作：{op}）" if op else "")
            + f"，目标：{self.target_of(agent, data)}"
        )
        req = ApprovalRequest(
            ticket_id=self.ticket_id,
            agent=agent,
            operation=op,
            target=self.target_of(agent, data),
            kind=kind,
            summary=summary,
        )
        return await self.gate.request(req)


def current_access() -> Optional[AccessContext]:
    """取当前访问上下文；未设置（直接访问的旧调用链）返回 None。"""
    return _ACCESS_VAR.get()


@asynccontextmanager
async def use_access(ctx: Optional[AccessContext]):
    """异步上下文：在协程及其派生任务中生效的访问上下文。"""
    token = _ACCESS_VAR.set(ctx)
    try:
        yield ctx
    finally:
        _ACCESS_VAR.reset(token)


def bind_access(ctx: Optional[AccessContext]):
    """同步绑定（REPL 等先 set 再 ``asyncio.run`` 的场景），返回 reset 令牌。"""
    return _ACCESS_VAR.set(ctx)


def reset_access(token) -> None:
    """复位 :func:`bind_access` 绑定的上下文。"""
    _ACCESS_VAR.reset(token)


# 性质 → 中文标签（tracker 事件用）
_KIND_LABELS = {"read": "只读", "write": "写入", "download": "下载", "install": "安装"}


async def gate_task(task) -> Optional[Any]:
    """执行收口共用的任务授权门。

    供 ``MultiAgentSystem.execute_task``（工作流/HTTP 单跑/REPL）与协调器直办
    路径在调用具体智能体前统一调用：

    :return: None 表示放行（direct 模式、无上下文或用户已批准）；
        非 None 为拒绝/超时转换出的 ``AgentResult(skipped=True)``。
    """
    from ..agents.base_agent import AgentResult
    from ..runtime import get_tracker

    ctx = current_access()
    if ctx is None or not ctx.is_sandbox:
        return None
    if task.data is None:
        task.data = {}
    if ctx.session is not None:
        task.data["_sandbox_session"] = ctx.session

    route = task.route_to if hasattr(task, "route_to") else task.name
    kind = ctx.classify(route, task.data)
    target = ctx.target_of(route, task.data)
    tracker = get_tracker()
    tracker.log("approval",
                f"等待授权：{route}（{_KIND_LABELS.get(kind, '未知')}）→ {target}",
                target)
    decision = await ctx.authorize(route, task.data)
    if not decision.approved:
        why = decision.reason or ("授权超时，已自动跳过"
                                  if decision.timeout else "用户拒绝授权")
        tracker.log("approval", f"任务跳过：{task.name}（{why}）", target)
        return AgentResult(success=False, skipped=True,
                           message=f"已跳过：{why}", errors=[why])
    tracker.log("approval", f"已获授权：{route} 开始执行", target)
    return None
