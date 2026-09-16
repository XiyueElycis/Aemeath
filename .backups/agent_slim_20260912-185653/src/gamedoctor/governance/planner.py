"""规划官（中书省角色）：把用户需求起草为可执行的任务 DAG。

两种起草方式，按成本与确定性排序：

1. **模板起草（0 次 LLM）**：接待官 ``detect_intent`` 已命中安装/排障/
   调优/维护模板时，直接取代码里锁死安全顺序的工作流模板。
2. **LLM 动态起草（1 次 LLM）**：模板未命中的复合/自由需求，由 LLM 在
   **已注册智能体目录**内生成阶段-任务 JSON，规划官解析回
   :class:`OrchestrationPlan`。智能体目录之外的名字一律不许出现——
   LLM 只能"选将"，不能"造将"，更不能决定安全红线（写操作前置顺序
   由审议官按规则复核）。

LLM 不可用 / JSON 无法解析 / 产出空计划时返回 ``plan=None``，由协调器
降级为单智能体决策路径（散点问答不该硬凑 DAG）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..orchestrator.orchestrator import (
    OrchestrationPlan,
    OrchestrationStage,
    _task,
    build_workflow,
)

# LLM 调用回调：输入 system/user 两段提示词，返回模型输出文本。
# 由协调器注入（实际绑定 HttpLLMClient.complete_messages），便于单测打桩。
LLMCall = Callable[[str, str], Awaitable[str]]


@dataclass
class PlanningResult:
    """规划官起草结果。"""

    plan: Optional[OrchestrationPlan]
    source: str = ""                 # template / llm / none
    goal: str = ""
    notes: List[str] = field(default_factory=list)


_PLAN_SYSTEM_TEMPLATE = """你是游戏辅助安装与排障体系的【规划官】，负责把用户需求起草为多智能体协作计划。

可用智能体（只能从下面挑，禁止编造不存在的名字）：
{catalog}

编排规则（必须遵守）：
1. 计划分为若干「阶段」，阶段间用 dependencies 声明依赖（按阶段名），可并行的调查类任务放进同一并行阶段。
2. 每个任务必须给唯一 name（英文 slug）、agent（上表注册名）、priority（数字，越小越早）、reason（一句话理由）。
3. requires 只能引用本计划内其他任务的 name；调查类（日志/兼容性/网络/性能/安全/环境）应并行先行。
4. 安全顺序铁律：涉及 Mod 安装/更新时，security_agent 与 update_manager 必须在 dlc_manager 之前；
   任何写操作之前必须先有 save_manager 备份；安装类任务以 installation 开头、以安装验证收尾。
5. 不确定是否需要的收尾增强任务（如性能复测、社区咨询）标记 optional=true。
6. 只输出一个 JSON 对象，不要输出其他文字：
{{
  "goal": "一句话目标",
  "stages": [
    {{"name": "阶段英文slug", "description": "这一阶段做什么（中文）", "parallel": true,
      "dependencies": [],
      "tasks": [
        {{"name": "任务slug", "agent": "智能体注册名", "priority": 10,
          "requires": [], "optional": false, "reason": "理由",
          "operation": "可选：read/list/edit/create/download，仅该智能体支持时填写",
          "file": "可选：目标路径", "url": "可选：下载地址"}}
      ]}}
  ]
}}
{mode_clause}
"""

_PLAN_MODE_CLAUSE = (
    "7. 当前为【方案模式】：只做只读调查。禁止规划 installation/dlc_manager/save_manager/"
    "update_manager/script_editor 等写操作智能体，禁止 download；可使用日志分析、兼容性、"
    "性能、网络、安全检测等只读智能体给出诊断方案。"
)
_EDIT_MODE_CLAUSE = "7. 当前为【编辑模式】：可规划含写操作的任务，但必须遵守第 4 条安全顺序铁律。"


def _extract_json(text: str) -> Dict[str, Any]:
    """从模型输出截取第一个完整 JSON 对象（容忍 markdown 围栏与闲聊包裹）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


class Planner:
    """规划官：模板兜底 + LLM 受约束动态起草。"""

    def __init__(self, orchestrator):
        self._orch = orchestrator

    # ------------------------------------------------------------------ #
    async def draft(
        self,
        *,
        intent: Optional[str],
        message: str,
        game_name: str,
        game_dir: str = "",
        agents_desc: str = "",
        llm_call: Optional[LLMCall] = None,
        mode: str = "edit",
        feedback: Optional[List[str]] = None,
    ) -> PlanningResult:
        """起草一轮计划。

        :param intent: 接待官正则命中的工作流模板键；非空且模板存在时直接走模板。
        :param feedback: 上一轮审议官的封驳意见（第 2/3 轮起草时附带），
            要求 LLM 针对性修订。
        :return: ``plan=None`` 表示无法成计划（调用方降级单智能体决策）。
        """
        # 1. 模板优先：确定性与安全顺序最强，零 LLM 成本
        if intent:
            plan = build_workflow(
                self._orch, intent, game_name,
                error_message=message, game_dir=game_dir,
            )
            return PlanningResult(
                plan=plan, source="template",
                goal=plan.description or plan.name,
                notes=[f"接待官命中模板「{intent}」，直接取确定性工作流（安全顺序代码锁死）"],
            )

        # 2. LLM 动态起草
        if llm_call is None:
            return PlanningResult(plan=None, source="none",
                                  notes=["无 LLM 通道，动态规划不可用"])
        return await self._draft_with_llm(
            message=message, game_name=game_name, game_dir=game_dir,
            agents_desc=agents_desc,
            llm_call=llm_call, mode=mode, feedback=feedback or [],
        )

    # ------------------------------------------------------------------ #
    async def _draft_with_llm(
        self, *, message: str, game_name: str, game_dir: str,
        agents_desc: str,
        llm_call: LLMCall, mode: str, feedback: List[str],
    ) -> PlanningResult:
        notes: List[str] = []
        mode_clause = _PLAN_MODE_CLAUSE if mode == "plan" else _EDIT_MODE_CLAUSE
        system = _PLAN_SYSTEM_TEMPLATE.format(catalog=agents_desc, mode_clause=mode_clause)
        user_parts = [f"用户需求：{message}"]
        if game_dir:
            user_parts.append(f"游戏文件根目录：{game_dir}")
        user_parts.append(f"游戏名：{game_name}")
        if feedback:
            user_parts.append(
                "上一轮计划被审议官封驳，请逐条解决以下问题后重新提交完整计划：\n- "
                + "\n- ".join(feedback)
            )
        try:
            raw = await llm_call(system, "\n".join(user_parts))
        except Exception as exc:  # noqa: BLE001  # LLM 故障统一降级，不阻断接待
            notes.append(f"规划官调用模型失败：{exc}")
            return PlanningResult(plan=None, source="none", notes=notes)

        spec = _extract_json(raw)
        stages_spec = spec.get("stages")
        if not isinstance(stages_spec, list) or not stages_spec:
            notes.append("模型未产出有效 stages，动态规划失败")
            return PlanningResult(plan=None, source="none", notes=notes)

        plan = self._build_plan_from_spec(
            spec, message=message, game_name=game_name, game_dir=game_dir,
            notes=notes,
        )
        if plan is None:
            return PlanningResult(plan=None, source="none", notes=notes)
        notes.append(f"规划官动态起草完成：{len(plan.stages)} 个阶段，"
                     f"{sum(len(s.tasks) for s in plan.stages)} 个任务")
        return PlanningResult(plan=plan, source="llm",
                              goal=str(spec.get("goal") or message[:40]), notes=notes)

    # ------------------------------------------------------------------ #
    def _build_plan_from_spec(
        self, spec: Dict[str, Any], *, message: str, game_name: str,
        game_dir: str, notes: List[str],
    ) -> Optional[OrchestrationPlan]:
        """把 LLM 的 JSON 规格转成 OrchestrationPlan（做格式规范化，不做安全审查）。

        安全/合法性问题留给审议官；此处仅处理：字段缺失容错、任务 name 去重、
        未知智能体原样保留（审议官会封驳并反馈给规划官重拟）。
        """
        base: Dict[str, Any] = {
            "game_name": game_name,
            "game_dir": game_dir,
            "user_message": message,
        }
        stages: List[OrchestrationStage] = []
        used_names: set[str] = set()
        stage_names: set[str] = set()

        for i, st in enumerate(spec["stages"]):
            if not isinstance(st, dict):
                continue
            st_name = str(st.get("name") or f"stage_{i + 1}").strip()
            # 阶段名去重
            if st_name in stage_names:
                st_name = f"{st_name}_{i + 1}"
                notes.append(f"阶段名重复，自动修正为 {st_name}")
            stage_names.add(st_name)

            tasks_spec = st.get("tasks")
            if not isinstance(tasks_spec, list) or not tasks_spec:
                notes.append(f"阶段「{st_name}」无任务，已丢弃该阶段")
                continue

            tasks = []
            for j, tspec in enumerate(tasks_spec):
                if not isinstance(tspec, dict):
                    continue
                agent = str(tspec.get("agent") or "").strip()
                t_name = str(tspec.get("name") or agent or f"task_{j + 1}").strip()
                if t_name in used_names:
                    t_name = f"{t_name}_{j + 1}"
                used_names.add(t_name)

                data = dict(base)
                # 透传决策类字段（operation/file/url/source/question）
                for key in ("operation", "file", "url", "source", "question"):
                    value = tspec.get(key)
                    if value:
                        data[key] = value
                reason = str(tspec.get("reason") or "").strip()
                if reason:
                    data["reason"] = reason

                try:
                    priority = int(tspec.get("priority", (j + 1) * 10))
                except (TypeError, ValueError):
                    priority = (j + 1) * 10

                tasks.append(_task(
                    agent or "unknown",
                    priority,
                    data,
                    requires=[str(r) for r in tspec.get("requires", []) if r],
                    optional=bool(tspec.get("optional", False)),
                    name=t_name,
                ))

            if tasks:
                deps = [str(d) for d in st.get("dependencies", []) if d]
                stages.append(OrchestrationStage(
                    name=st_name,
                    tasks=tasks,
                    dependencies=deps,
                    parallel=bool(st.get("parallel", len(tasks) > 1)),
                    description=str(st.get("description") or st_name),
                ))

        if not stages:
            return None
        return OrchestrationPlan(
            name="dynamic_plan",
            description=str(spec.get("goal") or message[:40]),
            stages=stages,
        )
