"""制度引擎：驱动「接待→规划→审议→派发→执行→验收→回奏」全链路。

:class:`GovernanceCoordinator` 是状态机的持有者与唯一驱动者，智能体自身
无权改任务状态。它把既有部件按三省六部制度重新串起来：

- 接待：``detect_intent`` 正则分拣 + 散点问答直办；
- 规划：:class:`~gamedoctor.governance.planner.Planner`（模板/LLM）；
- 审议：:class:`~gamedoctor.governance.reviewer.Reviewer`（可封驳 ≤3 轮）；
- 派发：每次派发经权限矩阵 :func:`can_dispatch` 校验；
- 执行：既有 ``GameAgentOrchestrator``（并行/门控/降级保持不变）；
- 回奏：复用 ``generate_reply`` 自然语言收口。

LLM 调用点维持「方案三」预算：模板路径 0 次规划调用（仅出口 1 次）；
动态规划路径入口 1 次 + 封驳每轮 1 次（至多 3 次）+ 出口 1 次。
审议是纯规则，永不调 LLM。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ..agents.base_agent import AgentTask
from ..config import app_dir
from ..errors import LLMError
from ..models import GameContext, to_dict
from ..orchestrator.orchestrator import (
    MUTATING_WORKFLOWS,
    WORKFLOW_LABELS,
    OrchestrationPlan,
    OrchestrationStage,
    _task,
    detect_intent,
)
from ..runtime import get_tracker
from ..sandbox import SandboxSession
from ..sandbox.session import register_session
from ..sandbox.context import (
    ACCESS_MODES,
    ACCESS_SANDBOX,
    AccessContext,
    use_access,
)
from .permissions import Role, can_dispatch
from .planner import Planner
from .reviewer import Reviewer, ReviewVerdict
from .state_machine import (
    MAX_REVIEW_ROUNDS,
    GovState,
    TaskTicket,
)

# 协调角色中文标签（状态流转用）
_INTAKE, _PLANNER, _REVIEWER, _DISPATCHER = "接待官", "规划官", "审议官", "调度官"


class GovernanceCoordinator:
    """持有一张任务票，按制度把它从 Intake 推到 Done。"""

    def __init__(self, manager: Any, settings: Any):
        self.manager = manager
        self.settings = settings
        self.planner = Planner(manager.orchestrator)
        self.reviewer = Reviewer()

    # ================================================================== #
    # 主链路
    # ================================================================== #
    async def run(
        self,
        message: str,
        *,
        client: Any,
        model: Optional[str],
        agents_desc: str,
        known_agents: Set[str],
        game_dir: str = "",
        enable_search: bool = False,
        think_level: str = "medium",
        mode: str = "edit",
        access_mode: str = "direct",
    ) -> Dict[str, Any]:
        """处理一轮对话。返回契约与旧 ``AgentChatService.chat`` 完全一致，
        额外在 result 中携带票号与流转日志。

        :param access_mode: 访问模式 ``direct``（直接访问，维持现状）/
            ``sandbox``（沙箱授权：任务逐个授权，写入先落沙箱，审核后应用）。
        """
        # 延迟导入避免 governance ↔ llm 循环依赖（generate_reply 用于回奏）
        from ..llm.agent_router import generate_reply

        if access_mode not in ACCESS_MODES:
            raise ValueError(f"未知访问模式：{access_mode}（可选：{ACCESS_MODES}）")

        ticket = TaskTicket(title=message[:30], mode=mode)
        mode_label = '编辑' if mode == 'edit' else '方案'
        access_label = '沙箱授权' if access_mode == ACCESS_SANDBOX else '直接访问'
        thinking: List[str] = [
            f"[接旨] 任务票 {ticket.ticket_id} 建立，当前【{mode_label}模式 / {access_label}】"
        ]

        # ---------- Intake：接待官分拣 ---------- #
        intent = detect_intent(message)
        ticket.intent = intent
        if intent is not None:
            label = WORKFLOW_LABELS.get(intent, intent)
            if mode == "plan" and intent in MUTATING_WORKFLOWS:
                # 方案模式硬拦截：含写操作的模板不立项
                ticket.transition(GovState.CANCELLED, _INTAKE, "—",
                                  f"方案模式拦截「{label}」写操作工作流")
                thinking.append(f"[接待] 命中「{label}」，但方案模式禁止写操作，已拒绝立项")
                return self._reply(
                    ticket, thinking,
                    reply=(
                        f"已识别到「{label}」意图，但当前为【方案模式】，"
                        "该工作流包含文件修改/下载等写操作，因此不会执行。"
                        "如需实际运行，请切换到「编辑模式」后再发送；"
                        "或换个说法（如报错/崩溃类问题），我可以先做只读排查。"
                    ),
                )
            thinking.append(f"[接待] 意图分拣命中「{label}」模板，移交规划官")
        else:
            thinking.append("[接待] 未命中固定模板，交由规划官评估是否需要多智能体协作")

        ticket.transition(GovState.PLANNING, _INTAKE, _PLANNER,
                          f"意图={intent or '自由需求'}")

        def llm_call(system: str, user: str):
            from ..llm.agent_router import THINK_LEVELS
            tcfg = THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])
            return asyncio.to_thread(
                client.complete_messages,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
                temperature=tcfg["temperature"],
                max_tokens=tcfg["max_tokens"],
            )

        game_name = Path(game_dir).name if game_dir else message[:24]

        # ---------- Planning ↔ Review 封驳循环 ---------- #
        feedback: List[str] = []
        draft = None
        verdict: Optional[ReviewVerdict] = None
        while True:
            draft = await self.planner.draft(
                intent=intent,
                message=message,
                game_name=game_name,
                game_dir=game_dir,
                agents_desc=agents_desc,
                llm_call=llm_call,
                mode=mode,
                feedback=feedback,
            )
            for n in draft.notes:
                ticket.note(n)
                thinking.append(f"[规划] {n}")

            if draft.plan is None:
                # 规划官封还：散点问答/动态规划失败，退接待官直办
                ticket.transition(GovState.INTAKE, _PLANNER, _INTAKE,
                                  "无法形成多智能体计划，封还接待官直办")
                thinking.append("[接待] 规划官封还（散点问答/模型不可用），改用单智能体决策直办")
                return await self._single_agent_path(
                    message, game_dir=game_dir, client=client, model=model,
                    agents_desc=agents_desc, known_agents=known_agents,
                    enable_search=enable_search,
                    think_level=think_level, mode=mode, ticket=ticket,
                    thinking=thinking, access_mode=access_mode,
                )

            ticket.plan = draft.plan
            ticket.plan_source = draft.source
            ticket.transition(GovState.REVIEW, _PLANNER, _REVIEWER,
                              f"第 {ticket.review_round} 轮方案提交审议（来源：{draft.source}）")
            thinking.append(f"[审议] 第 {ticket.review_round} 轮审议开始")

            verdict = self.reviewer.review(
                draft.plan, mode=mode, known_agents=known_agents,
            )
            ticket.review_verdicts.append(verdict)
            for w in verdict.warnings:
                thinking.append(f"[审议·提示] {w}")

            if verdict.approved:
                ticket.transition(GovState.ASSIGNED, _REVIEWER, _DISPATCHER, "准奏")
                thinking.append(f"[审议] 第 {ticket.review_round} 轮准奏")
                break

            # 封驳
            for b in verdict.blocking:
                thinking.append(f"[审议·封驳] {b}")
            # 模板是代码锁死的，不会因反馈改变：封驳即终止，不空转重拟
            if draft.source == "template":
                ticket.transition(GovState.CANCELLED, _REVIEWER, "—",
                                  f"模板计划封驳：{verdict.blocking[0]}")
                thinking.append("[审议] 固定模板不支持重拟，任务终止")
                return self._reply(
                    ticket, thinking,
                    reply=("固定工作流未通过安全审议，已停止执行：\n- "
                           + "\n- ".join(verdict.blocking[:6])
                           + "\n\n可在设置中启用被停用的必需智能体，或改用方案模式做只读排查。"),
                    error="review_rejected",
                )
            if ticket.review_round >= MAX_REVIEW_ROUNDS:
                # 硬伤超限：绝不强制通过，终止交付
                ticket.transition(GovState.CANCELLED, _REVIEWER, "—",
                                  f"历经 {MAX_REVIEW_ROUNDS} 轮审议仍有硬伤，终止")
                thinking.append(f"[审议] {MAX_REVIEW_ROUNDS} 轮封驳后仍有硬伤，任务终止（安全红线不强制通过）")
                return self._reply(
                    ticket, thinking,
                    reply=(
                        "该任务的执行计划经审议官三轮审查仍不合规，为安全起见已停止执行。\n"
                        "未通过的问题：\n- " + "\n- ".join(verdict.blocking[:6])
                        + "\n\n请调整需求后重试；只读排查类问题可切换到【方案模式】。"
                    ),
                    error="review_rejected",
                )
            feedback = verdict.blocking + verdict.suggestions
            ticket.transition(GovState.PLANNING, _REVIEWER, _PLANNER,
                              f"封驳：{verdict.blocking[0]} 等 {len(verdict.blocking)} 项硬伤")

        # ---------- Assigned：调度官权限校验后派发 ---------- #
        denied = self._enforce_dispatch(draft.plan, known_agents, thinking)
        if denied:
            ticket.transition(GovState.CANCELLED, _DISPATCHER, "—",
                              f"权限校验剔除全部任务：{denied[:2]}")
            return self._reply(ticket, thinking,
                               reply=f"派发被权限矩阵拦截：{denied[0]}，任务终止。",
                               error="dispatch_denied")

        context = GameContext(game_name=game_name)
        if game_dir:
            context.install_path = Path(game_dir)
        n_tasks = sum(len(s.tasks) for s in draft.plan.stages)
        ticket.transition(GovState.DOING, _DISPATCHER, "执行智能体集群",
                          f"派发 {n_tasks} 个任务，{len(draft.plan.stages)} 个阶段")
        thinking.append(f"[执行] 计划「{draft.plan.description or draft.plan.name}」开始")

        # 沙箱授权模式：建会话 + 上下文贯穿并行/串行全部任务
        session = None
        access_ctx = None
        if access_mode == ACCESS_SANDBOX:
            session = self._open_session(ticket.ticket_id, game_dir)
            access_ctx = AccessContext(
                access_mode=ACCESS_SANDBOX, ticket_id=ticket.ticket_id,
                game_name=game_name, game_dir=game_dir, session=session,
            )
            thinking.append("[沙箱] 沙箱授权模式：每个任务执行前需你授权，"
                            "文件改动先暂存沙箱，审核应用后才会真正落盘")
            get_tracker().log("phase", "沙箱模式：写入将在审核后落盘", game_dir)

        try:
            if access_ctx is not None:
                async with use_access(access_ctx):
                    outcome = await self.manager.orchestrator.execute_workflow(draft.plan, context)
            else:
                get_tracker().log("phase", "直接访问模式，跳过任务授权", game_dir)
                outcome = await self.manager.orchestrator.execute_workflow(draft.plan, context)
        except Exception as exc:  # noqa: BLE001  # 执行层任何故障都须兜底为任务取消，不可冒泡
            if session is not None:
                self._safe_seal(session)
            ticket.transition(GovState.CANCELLED, "执行智能体集群", "—", f"执行异常：{exc}")
            thinking.append(f"[执行] 工作流执行异常：{exc}")
            return self._reply(ticket, thinking,
                               reply=f"工作流执行出错：{exc}", error=str(exc),
                               sandbox=self._sandbox_payload(session))

        # ---------- Verification：审议官验收复审 ---------- #
        ticket.transition(GovState.VERIFICATION, "执行智能体集群", _REVIEWER,
                          "执行完毕，申请验收")
        counts = outcome.get("counts", {})
        thinking.append(
            f"[验收] {counts.get('succeeded', 0)} 成功 / "
            f"{counts.get('failed', 0)} 失败 / {counts.get('skipped', 0)} 跳过"
        )
        final_verdict = self.reviewer.review_outcome(outcome)
        for w in final_verdict.warnings:
            thinking.append(f"[验收·保留] {w}")
        ticket.outcome = outcome

        # ---------- Done：回奏 ---------- #
        ticket.transition(GovState.DONE, _REVIEWER, _INTAKE,
                          "验收通过（带保留）" if final_verdict.warnings else "验收通过")
        if session is not None:
            self._safe_seal(session)
        summary_json = _workflow_summary_json(outcome)
        display = f"协作计划「{draft.plan.description or draft.plan.name}」"
        try:
            reply = await asyncio.to_thread(
                generate_reply, client, message, display, summary_json, model,
                game_dir, think_level, mode,
            )
        except LLMError as exc:
            reply = (
                f"协作计划已执行完成（{counts.get('succeeded', 0)} 成功 / "
                f"{counts.get('failed', 0)} 失败 / {counts.get('skipped', 0)} 跳过），"
                f"但生成解释失败：{exc}"
            )
        sandbox_note = self._sandbox_note(session)
        if sandbox_note:
            reply = f"{reply}\n\n{sandbox_note}"
        thinking.append("[回奏] " + (reply[:200] + "…" if len(reply) > 200 else reply))

        return self._reply(
            ticket, thinking, reply=reply,
            agent_used=f"workflow:{intent or draft.source}",
            result_extra={
                "plan": outcome.get("plan_name"),
                "success": outcome.get("success"),
                "counts": counts,
                "failed_tasks": outcome.get("failed_tasks", []),
                "summary": outcome.get("summary", ""),
            },
            sandbox=self._sandbox_payload(session),
        )

    # ================================================================== #
    # 散点问答直办路径（规划官封还后，接待官奉旨单办）
    # ================================================================== #
    async def _single_agent_path(
        self, message: str, *, game_dir: str, client: Any, model: Optional[str],
        agents_desc: str, known_agents: Set[str], enable_search: bool,
        think_level: str, mode: str,
        ticket: TaskTicket, thinking: List[str],
        access_mode: str = "direct",
    ) -> Dict[str, Any]:
        """LLM 单智能体决策路径（原 AgentChatService 主干逻辑，制度上属接待官直办）。"""
        from ..llm.agent_router import (
            MODE_PLAN,
            _is_mutating,
            decide_agent,
            generate_reply,
        )

        try:
            decision = await asyncio.to_thread(
                decide_agent, client, message, agents_desc, model,
                game_dir, think_level, mode,
            )
        except LLMError as exc:
            ticket.transition(GovState.DONE, _INTAKE, _INTAKE, "决策失败，直答")
            thinking.append(f"[决策失败] {exc}")
            return self._reply(ticket, thinking, reply=f"大模型调用失败：{exc}",
                               error=str(exc))

        reason = (decision.get("reason") or "").strip()
        if reason:
            thinking.append(f"[思考] {reason}")
        agent_name = decision.get("agent")
        thinking.append("[决策] " + (f"调用智能体 {agent_name}" if agent_name else "直接回答，不调用智能体"))

        if not agent_name:
            ticket.transition(GovState.DONE, _INTAKE, _INTAKE, "闲聊直答")
            return self._reply(ticket, thinking,
                               reply=decision.get("reply") or "我没有找到合适的处理方式。")

        if agent_name not in self.manager.agents:
            ticket.transition(GovState.CANCELLED, _INTAKE, "—", f"未知智能体 {agent_name}")
            return self._reply(ticket, thinking, reply=f"未知智能体：{agent_name}",
                               error="unknown_agent")
        if not self.settings.is_agent_enabled(agent_name):
            ticket.transition(GovState.CANCELLED, _INTAKE, "—", f"智能体 {agent_name} 已停用")
            return self._reply(ticket, thinking,
                               reply=f"智能体「{self.manager.get_display_name(agent_name)}」已被禁用。",
                               error="agent_disabled")
        if agent_name == "web_search" and not enable_search:
            ticket.transition(GovState.CANCELLED, _INTAKE, "—", "联网搜索未开启")
            return self._reply(ticket, thinking,
                               reply="联网搜索未开启，请在输入框左侧打开「联网搜索」开关后再试。",
                               error="search_disabled")

        operation = (decision.get("operation") or "").lower()
        if mode == MODE_PLAN and _is_mutating(agent_name, operation):
            ticket.transition(GovState.CANCELLED, _INTAKE, "—", "方案模式拦截写操作")
            thinking.append("[决策] 方案模式下阻止了文件修改/下载操作")
            return self._reply(
                ticket, thinking,
                reply="当前为【方案模式】，已阻止对文件执行新建/修改/删除/替换/下载操作。"
                      "如需真正改动文件，请切换到「编辑模式」；我先以只读方式调查并给出方案。",
                error="plan_mode_block",
            )

        # 权限校验：接待官直办的单任务派发同样过矩阵
        ok, why = can_dispatch(Role.DISPATCHER, agent_name,
                               set(self.manager.agents.keys()))
        if not ok:
            ticket.transition(GovState.CANCELLED, _INTAKE, "—", why)
            return self._reply(ticket, thinking, reply=f"派发被拦截：{why}",
                               error="dispatch_denied")

        # 接待官直办也不可越级：补立项 → 审议 → 派发 → 执行 → 验收 → 回奏，
        # 逐跳走状态机（票据此前因规划官封还停在 Intake）。
        display = self.manager.get_display_name(agent_name)
        data: Dict[str, Any] = {
            "game_name": Path(game_dir).name if game_dir else "Unknown",
            "game_dir": game_dir,
            "user_message": decision.get("question") or message,
        }
        for key, value in (("operation", operation),
                           ("file", decision.get("file") or ""),
                           ("url", decision.get("url") or ""),
                           ("source", decision.get("source") or "")):
            if value:
                data[key] = value
        plan = OrchestrationPlan(
            name="single_direct",
            description=f"接待官直办：{display} 处理「{message[:24]}」",
            stages=[OrchestrationStage(
                name="direct",
                tasks=[_task(agent_name, 10, data, name=agent_name)],
                parallel=False,
                description=f"单任务派发：{display}",
            )],
        )
        ticket.transition(GovState.PLANNING, _INTAKE, _PLANNER, "接待官直办：单任务补立项")
        ticket.plan = plan
        ticket.plan_source = "single"
        ticket.transition(GovState.REVIEW, _PLANNER, _REVIEWER, "单任务方案提交审议")
        direct_verdict = self.reviewer.review(plan, mode=mode, known_agents=known_agents)
        if not direct_verdict.approved:
            ticket.transition(GovState.CANCELLED, _REVIEWER, "—",
                              f"单任务审议封驳：{direct_verdict.blocking[0]}")
            return self._reply(
                ticket, thinking,
                reply="单任务方案未通过安全审议，已停止：\n- "
                      + "\n- ".join(direct_verdict.blocking[:6]),
                error="review_rejected",
            )
        for w in direct_verdict.warnings:
            thinking.append(f"[审议·提示] {w}")
        ticket.transition(GovState.ASSIGNED, _REVIEWER, _DISPATCHER, "准奏：单任务派发")
        ticket.transition(GovState.DOING, _DISPATCHER, f"智能体「{agent_name}」",
                          f"单任务派发：{operation or 'execute'}")
        thinking.append(f"[执行] {display} 开始运行")

        # 沙箱授权模式：建会话并经上下文注入（授权门在统一执行收口处生效）
        session = None
        access_ctx = None
        if access_mode == ACCESS_SANDBOX:
            game_name = Path(game_dir).name if game_dir else "Unknown"
            session = self._open_session(ticket.ticket_id, game_dir)
            access_ctx = AccessContext(
                access_mode=ACCESS_SANDBOX, ticket_id=ticket.ticket_id,
                game_name=game_name, game_dir=game_dir, session=session,
            )
            thinking.append("[沙箱] 沙箱授权模式：该任务执行前需你授权，"
                            "文件改动先暂存沙箱，审核应用后才会真正落盘")
            get_tracker().log("phase", "沙箱模式：写入将在审核后落盘", game_dir)

        try:
            if access_ctx is not None:
                async with use_access(access_ctx):
                    result = await self._execute_agent(
                        agent_name, decision.get("question") or message, game_dir,
                        operation, decision.get("file") or "",
                        decision.get("url") or "", decision.get("source") or "",
                    )
            else:
                get_tracker().log("phase", "直接访问模式，跳过任务授权", game_dir)
                result = await self._execute_agent(
                    agent_name, decision.get("question") or message, game_dir,
                    operation, decision.get("file") or "",
                    decision.get("url") or "", decision.get("source") or "",
                )
        except Exception as exc:  # noqa: BLE001  # 执行层异常兜底为任务取消，不冒泡成 500
            if session is not None:
                self._safe_seal(session)
            ticket.transition(GovState.CANCELLED, f"智能体「{agent_name}」", "—",
                              f"执行异常：{exc}")
            thinking.append(f"[执行] {display} 执行异常：{exc}")
            return self._reply(ticket, thinking,
                               reply=f"智能体执行出错：{exc}", error=str(exc),
                               sandbox=self._sandbox_payload(session))
        if session is not None:
            self._safe_seal(session)
        thinking.append(f"[执行] {display} 完成：{result.message}")
        ticket.transition(GovState.VERIFICATION, f"智能体「{agent_name}」", _REVIEWER,
                          "单任务回报，申请验收")

        result_json = json.dumps(to_dict(result), ensure_ascii=False, default=str)
        try:
            reply = await asyncio.to_thread(
                generate_reply, client, message, display, result_json, model,
                game_dir, think_level, mode,
            )
        except LLMError as exc:
            reply = f"智能体已执行完成，但生成回复失败：{exc}"
        sandbox_note = self._sandbox_note(session)
        if sandbox_note:
            reply = f"{reply}\n\n{sandbox_note}"
        thinking.append("[回奏] " + (reply[:200] + "…" if len(reply) > 200 else reply))

        ticket.transition(GovState.DONE, _REVIEWER, _INTAKE, "验收通过，回奏")
        return self._reply(ticket, thinking, reply=reply, agent_used=agent_name,
                           result=to_dict(result),
                           sandbox=self._sandbox_payload(session))

    # ================================================================== #
    # 辅助
    # ================================================================== #
    def _enforce_dispatch(self, plan: Any, known_agents: Set[str],
                          thinking: List[str]) -> List[str]:
        """调度官派发前逐任务过权限矩阵；非法任务从计划中剔除。

        :return: 被剔除任务的说明列表；全部任务被剔除时调用方终止任务。
        """
        denied: List[str] = []
        for st in plan.stages:
            kept = []
            for t in st.tasks:
                ok, why = can_dispatch(Role.DISPATCHER, t.agent, known_agents)
                if ok:
                    kept.append(t)
                else:
                    denied.append(f"任务「{t.name}」：{why}")
                    thinking.append(f"[调度·拦截] {why}")
            st.tasks = kept
        if not denied:
            thinking.append("[调度] 全部任务通过权限矩阵校验，准予派发")
        else:
            thinking.append(f"[调度] 权限校验剔除 {len(denied)} 个非法任务")
        return denied

    async def _execute_agent(self, name: str, question: str, game_dir: str,
                             operation: str = "", file_ref: str = "",
                             url: str = "", source: str = ""):
        """执行单个智能体并同步运行状态追踪器。

        沙箱模式下先过与编排收口**同一道**任务授权门（:func:`gate_task`），
        随后仍直接 ``agent.execute``——保留"执行层异常冒泡为任务取消"的
        既有语义（不像 MultiAgentSystem 那样吞异常转失败结果）。
        """
        from ..sandbox.context import gate_task

        agent = self.manager.get_agent(name)
        display = self.manager.get_display_name(name)
        tracker = get_tracker()
        path = game_dir or ""
        tracker.set_current(name, display, "运行中", path)
        tracker.log("access", f"正在访问目录：{path}" if path else "未指定游戏目录", path)

        game_name = Path(game_dir).name if game_dir else "Unknown"
        data: Dict[str, Any] = {
            "game_name": game_name,
            "game_dir": game_dir,
            "user_message": question,
        }
        for key, value in (("operation", operation), ("file", file_ref),
                           ("url", url), ("source", source)):
            if value:
                data[key] = value
        task = AgentTask(
            name=name,
            required_capabilities=agent.get_capabilities()[:1] or [],
            data=data,
        )
        try:
            skipped = await gate_task(task)
            if skipped is not None:
                return skipped
            result = await agent.execute(task)
        finally:
            tracker.clear_current()
        tracker.log("phase", f"{display} 执行完成", path)
        return result

    # ------------------------------------------------------------------ #
    # 沙箱会话生命周期
    # ------------------------------------------------------------------ #
    @staticmethod
    def _open_session(ticket_id: str, game_dir: str) -> SandboxSession:
        """为本轮对话创建沙箱会话。

        有游戏目录时以其为授权根；未提供时建一个票据专属空根，此时任何写都
        落在 side 区，审核应用时回到真实位置。
        """
        if game_dir:
            root = Path(game_dir)
        else:
            root = app_dir() / "sandbox" / f"{ticket_id}_empty_root"
            root.mkdir(parents=True, exist_ok=True)
        session = SandboxSession(ticket_id, root)
        register_session(session)
        return session

    @staticmethod
    def _safe_seal(session: SandboxSession) -> None:
        """封存会话（running→ready）供审核；封存失败不影响主链路回奏。"""
        try:
            session.seal()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("gamedoctor.governance").warning(
                "沙箱会话 %s 封存失败：%s", session.ticket_id, exc)

    @staticmethod
    def _sandbox_payload(session: Optional[SandboxSession]) -> Optional[Dict[str, Any]]:
        """对话响应中的 sandbox 字段（direct 模式为 None）。"""
        if session is None:
            return None
        info = session.describe()
        return {
            "ticket_id": session.ticket_id,
            "status": session.status,
            "change_count": len(session.changes),
            "changes": info.get("changes", []),
        }

    @staticmethod
    def _sandbox_note(session: Optional[SandboxSession]) -> str:
        """沙箱模式回奏话术后缀（FR-16）；无变更时不提示审核。"""
        if session is None:
            return ""
        count = len(session.changes)
        if count == 0:
            return "【沙箱授权】本次操作没有产生任何文件变更。"
        return (
            f"【沙箱授权】{count} 项变更已暂存在沙箱中，尚未真正写入磁盘。"
            "请在「变更审核」面板核对后选择「应用全部变更」或「丢弃」；"
            "应用前原文件会自动备份。"
        )

    @staticmethod
    def _reply(ticket: TaskTicket, thinking: List[str], *, reply: str,
               agent_used: Optional[str] = None, result: Any = None,
               result_extra: Optional[Dict[str, Any]] = None,
               sandbox: Optional[Dict[str, Any]] = None,
               error: Optional[str] = None) -> Dict[str, Any]:
        """统一构造返回契约，并附带票号/流转日志（供未来任务看板使用）。"""
        payload: Dict[str, Any] = {
            "reply": reply,
            "agent_used": agent_used,
            "result": result if result is not None else result_extra,
            "thinking": thinking,
            "sandbox": sandbox,
            "ticket": {
                "id": ticket.ticket_id,
                "state": ticket.state.value,
                "intent": ticket.intent,
                "plan_source": ticket.plan_source,
                "review_rounds": ticket.review_round,
                "forced_approved": ticket.forced_approved,
                "flow_log": [f.to_dict() for f in ticket.flow_log],
            },
        }
        if error:
            payload["error"] = error
        return payload


def _workflow_summary_json(outcome: Dict[str, Any]) -> str:
    """压缩工作流结果为回喂 LLM 的精简 JSON（与旧 AgentChatService 行为一致）。"""
    stages: Dict[str, Any] = {}
    for stage_name, results in (outcome.get("stages") or {}).items():
        if not isinstance(results, dict):
            continue
        stages[stage_name] = {
            name: {
                "success": bool(r.success),
                "skipped": bool(r.skipped),
                "message": (r.message or "")[:200],
            }
            for name, r in results.items()
        }
    brief = {
        "plan": outcome.get("plan_name"),
        "success": outcome.get("success"),
        "counts": outcome.get("counts"),
        "failed_tasks": outcome.get("failed_tasks", []),
        "stages": stages,
    }
    return json.dumps(brief, ensure_ascii=False, default=str)
