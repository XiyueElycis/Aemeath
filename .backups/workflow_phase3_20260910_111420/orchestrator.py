"""智能体编排器。

负责管理和协调多个智能体的工作，实现任务分配和结果聚合。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..agents.base_agent import AgentTask, AgentResult, BaseAgent, MultiAgentSystem
from ..models import GameContext, DiagnosisReport
from ..runtime import get_tracker


@dataclass
class OrchestrationPlan:
    """编排计划。"""
    name: str
    description: str
    stages: List[OrchestrationStage]
    parallel_execution: bool = True


@dataclass
class OrchestrationStage:
    """编排阶段。

    :param description: 面向用户的中文说明，用于进度回显与报告，
        让非技术用户看懂这一步在做什么。
    """
    name: str
    tasks: List[AgentTask]
    dependencies: List[str] = None  # 依赖的阶段名
    parallel: bool = True
    description: str = ""

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []


def _task(agent_name: str, priority: int, data: Dict[str, Any],
          requires: List[str] = None, optional: bool = False,
          name: str = "") -> AgentTask:
    """构造一个「按智能体名直接路由」的任务。

    刻意**不设** ``required_capabilities``：多个智能体的能力存在重叠
    （如 ``file_read`` 同属日志分析与脚本编辑，``compatibility`` 同属兼容性与
    安装规划的能力语义），靠能力匹配可能选错；而智能体注册名唯一，
    直接路由最可靠。

    :param agent_name: 智能体注册名，须与 ``BaseAgent.name`` 一致。
    :param priority: 阶段内排序用，越小越先（仅对串行阶段有意义）。
    :param data: 传给智能体的数据，各工作流共享同一份基础字段。
    :param requires: 前置任务名（``task.name``），任一失败/跳过则本任务跳过。
    :param optional: 可选任务，失败或被跳过不计入工作流整体成败。
    :param name: 任务的唯一标识，缺省与 ``agent_name`` 相同。同一工作流内
        多次调用同一智能体时（如性能基线/复测）必须显式指定不同 name，
        否则结果字典与依赖声明会因键冲突而互相覆盖。
    """
    # 每个任务持有独立的 data 副本，避免并行阶段内任务互相污染入参
    return AgentTask(
        name=name or agent_name,
        agent=agent_name,
        priority=priority,
        data=dict(data),
        requires=list(requires or []),
        optional=optional,
    )


class GameAgentOrchestrator:
    """游戏智能体编排器。"""

    def __init__(self):
        self.agent_system = MultiAgentSystem()
        self.logger = logging.getLogger("gamedoctor.orchestrator")
        self.active_plans: Dict[str, OrchestrationPlan] = {}

    def register_agents(self, agents: List[BaseAgent]) -> None:
        """注册所有智能体。"""
        for agent in agents:
            self.agent_system.register_agent(agent)

    async def execute_workflow(self, plan: OrchestrationPlan, context: GameContext) -> Dict[str, Any]:
        """执行编排计划。

        阶段之间串行、阶段内部可并行。门控有两层：

        - **阶段级** ``stage.dependencies``：前置阶段整体失败则跳过该阶段。
        - **任务级** ``task.requires``：前置任务失败/跳过则只跳过该任务，
          同阶段其余任务照常执行——避免一个只读采集失败就连带阻断下游。

        全部任务的执行结果扁平记录在 ``task_results`` 中，供跨阶段的任务级
        门控查询（后续阶段的任务可以依赖更早阶段的任务）。
        """
        self.logger.info(f"Starting workflow: {plan.name}")
        tracker = get_tracker()
        tracker.log("phase", f"工作流开始：{plan.description or plan.name}")

        stage_results: Dict[str, Any] = {}
        task_results: Dict[str, AgentResult] = {}
        total_stages = len(plan.stages)

        for idx, stage in enumerate(plan.stages, start=1):
            # 阶段级依赖检查
            if not self._check_dependencies(stage, stage_results, plan):
                self.logger.warning(f"Skipping stage {stage.name} due to unmet dependencies")
                tracker.log("phase", f"阶段 {idx}/{total_stages}「{stage.name}」已跳过（前置阶段未满足）")
                stage_results[stage.name] = {
                    t.name: self._skipped_result(t, f"前置阶段未满足：{', '.join(stage.dependencies)}")
                    for t in stage.tasks
                }
                task_results.update(stage_results[stage.name])
                continue

            tracker.log("phase", f"阶段 {idx}/{total_stages}「{stage.name}」开始（{len(stage.tasks)} 个任务）")

            if stage.parallel and len(stage.tasks) > 1:
                results = await self._execute_stage_parallel(stage, context, task_results)
            else:
                results = await self._execute_stage_sequential(stage, context, task_results)

            task_results.update(results)
            stage_results[stage.name] = results

        # 聚合最终结果
        final_result = self._aggregate_results(plan, stage_results)
        final_result["task_results"] = task_results

        self.logger.info(f"Workflow {plan.name} completed")
        tracker.log("phase", f"工作流结束：{final_result['summary']}")
        return final_result

    def _skipped_result(self, task: AgentTask, reason: str) -> AgentResult:
        """构造一条「被门控跳过」的结果。"""
        return AgentResult(success=False, skipped=True, message=f"已跳过：{reason}")

    def _requires_met(self, task: AgentTask, task_results: Dict[str, AgentResult]) -> bool:
        """检查任务级前置依赖是否满足。

        前置任务缺失、失败或已被跳过，都视为未满足。
        """
        for dep in task.requires:
            dep_result = task_results.get(dep)
            if dep_result is None or not dep_result.success:
                return False
        return True

    async def _execute_stage_parallel(self, stage: OrchestrationStage, context: GameContext,
                                      task_results: Dict[str, AgentResult]) -> Dict[str, AgentResult]:
        """并行执行阶段中的任务。

        注意：并行阶段内的任务之间不应互相 ``requires``（同一批同时起跑，
        彼此的结果都还不存在）。任务级依赖只在「跨阶段」或串行阶段内有意义。
        """
        runnable: List[AgentTask] = []
        skipped: Dict[str, AgentResult] = {}
        for task in stage.tasks:
            if not self._requires_met(task, task_results):
                missing = [d for d in task.requires
                           if d not in task_results or not task_results[d].success]
                skipped[task.name] = self._skipped_result(
                    task, f"前置任务未完成：{', '.join(missing) or '未知'}")
                continue
            task.context = context
            runnable.append(task)

        coros = [self._execute_task_with_context(task, context) for task in runnable]
        results = await asyncio.gather(*coros, return_exceptions=True)

        out: Dict[str, AgentResult] = dict(skipped)
        for task, result in zip(runnable, results):
            out[task.name] = self._handle_result(result, task)
        return out

    async def _execute_stage_sequential(self, stage: OrchestrationStage, context: GameContext,
                                        task_results: Dict[str, AgentResult]) -> Dict[str, AgentResult]:
        """串行执行阶段中的任务。

        串行阶段内逐个执行，且每完成一个就并入 ``task_results``，
        因此同阶段后续任务可以依赖前面任务的结果。
        """
        results: Dict[str, AgentResult] = {}
        local = dict(task_results)
        for task in stage.tasks:
            if not self._requires_met(task, local):
                missing = [d for d in task.requires
                           if d not in local or not local[d].success]
                results[task.name] = self._skipped_result(
                    task, f"前置任务未完成：{', '.join(missing) or '未知'}")
                local[task.name] = results[task.name]
                continue

            task.context = context
            result = await self._execute_task_with_context(task, context)
            results[task.name] = self._handle_result(result, task)
            local[task.name] = results[task.name]
        return results

    async def execute_task(self, task: AgentTask) -> AgentResult:
        """执行单个任务（供 CLI / 测试等需要单一任务结果的场景直接调用）。"""
        return await self.agent_system.execute_task(task)

    async def _execute_task_with_context(self, task: AgentTask, context: GameContext) -> AgentResult:
        """执行单个任务，并注入游戏上下文。"""
        if task.data is None:
            task.data = {}
        task.data.update({
            "game_context": context,
            "timestamp": time.time()
        })

        tracker = get_tracker()
        tracker.log("phase", f"任务开始：{task.name}")
        started = time.perf_counter()
        try:
            return await self.agent_system.execute_task(task)
        finally:
            elapsed = time.perf_counter() - started
            tracker.log("phase", f"任务结束：{task.name}（{elapsed:.2f}s）")

    def _handle_result(self, result: Any, task: AgentTask) -> AgentResult:
        """处理任务结果。"""
        if isinstance(result, Exception):
            return AgentResult(
                success=False,
                message=f"Task {task.name} failed: {str(result)}",
                errors=[str(result)]
            )
        return result

    def _check_dependencies(self, stage: OrchestrationStage, stage_results: Dict[str, Any],
                            plan: OrchestrationPlan | None = None) -> bool:
        """检查阶段依赖是否满足。

        判定口径：前置阶段中每个**必选**任务都必须成功。
        - 必选任务失败 → 阻断（下游拿不到它的前置数据）
        - 必选任务被跳过 → 阻断（说明更上游已断链）
        - ``optional`` 任务失败或跳过 → 不阻断（增益项，不影响主流程）

        未传 ``plan`` 时无法区分可选性，退回到保守口径（要求全部成功）。
        """
        for dep_name in stage.dependencies:
            if dep_name not in stage_results:
                return False
            dep_result = stage_results[dep_name]
            if not isinstance(dep_result, dict):
                if not dep_result.success:
                    return False
                continue
            for name, r in dep_result.items():
                task = self._task_of(plan, name) if plan else None
                if task is not None and task.optional:
                    continue
                if not r.success:
                    return False
        return True

    def _aggregate_results(self, plan: OrchestrationPlan, stage_results: Dict[str, Any]) -> Dict[str, Any]:
        """聚合所有阶段的结果。

        整体成败只统计「必选且实际执行」的任务：``optional`` 任务与 ``skipped``
        任务不计入分母，否则一个被安全门控正常拦下的任务会让整条工作流显示失败。
        """
        final_result = {
            "plan_name": plan.name,
            "success": True,
            "stages": stage_results,
            "summary": self._generate_summary(stage_results)
        }

        counts = {"total": 0, "succeeded": 0, "failed": 0, "skipped": 0, "optional_failed": 0}
        mandatory_failures: List[str] = []

        for results in stage_results.values():
            if not isinstance(results, dict):
                continue
            for name, r in results.items():
                task = self._task_of(plan, name)
                optional = bool(task and task.optional)
                counts["total"] += 1
                if r.skipped:
                    counts["skipped"] += 1
                elif r.success:
                    counts["succeeded"] += 1
                elif optional:
                    counts["optional_failed"] += 1
                else:
                    counts["failed"] += 1
                    mandatory_failures.append(name)

        final_result["counts"] = counts
        final_result["failed_tasks"] = mandatory_failures
        final_result["success"] = not mandatory_failures
        return final_result

    @staticmethod
    def _task_of(plan: OrchestrationPlan, task_name: str) -> Optional[AgentTask]:
        """按计划查找任务定义，找不到返回 ``None``。"""
        for stage in plan.stages:
            for task in stage.tasks:
                if task.name == task_name:
                    return task
        return None

    def _generate_summary(self, stage_results: Dict[str, Any]) -> str:
        """生成执行摘要。"""
        successful_stages = 0
        total_stages = len(stage_results)

        for stage_name, results in stage_results.items():
            if isinstance(results, dict):
                if all(r.success for r in results.values()):
                    successful_stages += 1
            elif results.success:
                successful_stages += 1

        return f"Completed {successful_stages}/{total_stages} stages successfully"

    def create_diagnostic_workflow(self, game_name: str, error_message: str,
                                   game_dir: str = "") -> OrchestrationPlan:
        """创建排障诊断工作流（以只读采集为主，不改动文件）。

        顺序意图：先拿技术栈基线与日志证据，再按证据类型分头核查安全/网络/性能，
        最后聚合社区已知解法。全程只读，可安全重复执行。
        """
        base = {"game_name": game_name, "game_dir": game_dir,
                "error_message": error_message}
        return OrchestrationPlan(
            name="diagnostic_workflow",
            description="游戏报错诊断（只读排查）",
            stages=[
                OrchestrationStage(
                    name="collect",
                    description="只读采集：技术栈基线 + 日志证据",
                    tasks=[
                        _task("compatibility", 10, base),
                        _task("log_analyzer", 10, base),
                    ],
                ),
                OrchestrationStage(
                    name="inspect",
                    description="按证据分头核查：安全 / 网络 / 性能",
                    dependencies=["collect"],
                    tasks=[
                        _task("security_agent", 20, base, requires=["compatibility"]),
                        _task("network_expert", 20, base, requires=["compatibility"]),
                        _task("performance_analyst", 20, base, requires=["compatibility"]),
                    ],
                ),
                OrchestrationStage(
                    name="resolve",
                    description="聚合社区已知解法（增益项）",
                    dependencies=["inspect"],
                    tasks=[
                        _task("community_agent", 30, base,
                              requires=["log_analyzer"], optional=True),
                    ],
                ),
            ],
        )

    def create_installation_workflow(self, game_name: str, error_message: str = "",
                                     game_dir: str = "") -> OrchestrationPlan:
        """创建完整安装工作流（13 个确定性智能体全链路）。

        顺序不可调换的关键约束：

        - ``security_agent`` 必须在 ``dlc_manager`` 之前：反作弊兼容性未通过就装
          Mod 有封号风险（PRD §4.7 高危拦截）。
        - ``save_manager`` 必须在任何写操作之前：PRD §4.7「任何写操作前必备份」。
        - ``update_manager`` 必须在 ``dlc_manager`` 之前：后者依赖前者定下的版本基线
          做 ``version_sync``。
        - ``social_agent`` 必须在 ``network_expert`` 之后：语音聊天走网络通道。

        同类项（采集组 / 调优组 / 联机社交组）内部无先后，已标记为可并行或可选。
        """
        base = {"game_name": game_name, "game_dir": game_dir}
        return OrchestrationPlan(
            name="installation_workflow",
            description="游戏安装全链路（采集 → 安装 → 调优 → 联机社交）",
            stages=[
                # 阶段一：只读采集（同类，可并行，谁先谁后不影响结果）
                OrchestrationStage(
                    name="collect",
                    description="只读采集：技术栈基线 + 既有日志",
                    tasks=[
                        _task("compatibility", 10, base),
                        _task("log_analyzer", 10, base, optional=True),
                    ],
                ),
                # 阶段二：安装链路（严格串行，顺序不可调换）
                OrchestrationStage(
                    name="install",
                    description="安装链路：安装 → 定版本 → 安全 → 备份 → Mod",
                    dependencies=["collect"],
                    parallel=False,
                    tasks=[
                        _task("installation", 10, base, requires=["compatibility"]),
                        _task("update_manager", 20, base, requires=["installation"]),
                        _task("security_agent", 30, base, requires=["update_manager"]),
                        _task("save_manager", 40, base, requires=["security_agent"]),
                        _task("dlc_manager", 50, base, requires=["save_manager"]),
                    ],
                ),
                # 阶段三：运行调优（同类，audio 独立可提前）
                OrchestrationStage(
                    name="tune",
                    description="运行调优：音频 / 启动参数 / 性能瓶颈",
                    dependencies=["install"],
                    parallel=False,
                    tasks=[
                        _task("audio_expert", 10, base),
                        _task("launch_optimizer", 20, base),
                        _task("performance_analyst", 30, base,
                              requires=["launch_optimizer"]),
                    ],
                ),
                # 阶段四：联机与社交（social 依赖 network，community 为兜底增益）
                OrchestrationStage(
                    name="online",
                    description="联机与社交：网络 → 社交 → 社区资源",
                    dependencies=["install"],
                    parallel=False,
                    tasks=[
                        _task("network_expert", 10, base),
                        _task("social_agent", 20, base, requires=["network_expert"]),
                        _task("community_agent", 30, base, optional=True),
                    ],
                ),
            ],
        )

    def create_optimization_workflow(self, game_name: str, error_message: str = "",
                                     game_dir: str = "") -> OrchestrationPlan:
        """创建性能调优工作流（面向「游戏能跑但卡/糊/没声」）。

        前提：游戏已安装，因此跳过安装链路，直接从兼容性基线进入调优。
        """
        base = {"game_name": game_name, "game_dir": game_dir}
        return OrchestrationPlan(
            name="optimization_workflow",
            description="性能与体验调优",
            stages=[
                OrchestrationStage(
                    name="baseline",
                    description="建立基线：兼容性 + 当前性能表现",
                    tasks=[
                        _task("compatibility", 10, base),
                        _task("performance_analyst", 10, base, name="perf_baseline"),
                    ],
                ),
                OrchestrationStage(
                    name="tune",
                    description="调优：启动参数 / 音频 / 网络",
                    dependencies=["baseline"],
                    tasks=[
                        # 启动参数先定，性能复测才有稳定基准
                        _task("launch_optimizer", 20, base, requires=["perf_baseline"]),
                        _task("audio_expert", 20, base),
                        _task("network_expert", 20, base),
                    ],
                ),
                OrchestrationStage(
                    name="verify",
                    description="复测性能，对比调优前后",
                    dependencies=["tune"],
                    parallel=False,
                    tasks=[
                        _task("performance_analyst", 30, base,
                              name="perf_recheck", requires=["launch_optimizer"]),
                    ],
                ),
            ],
        )

    def create_maintenance_workflow(self, game_name: str, error_message: str = "",
                                     game_dir: str = "") -> OrchestrationPlan:
        """创建日常维护工作流（更新 / 备份 / Mod 同步 / 安全巡检）。

        顺序约束同安装链路：先定版本基线，再做安全巡检，备份在写操作之前。
        """
        base = {"game_name": game_name, "game_dir": game_dir}
        return OrchestrationPlan(
            name="maintenance_workflow",
            description="日常维护（更新 / 备份 / Mod / 安全）",
            stages=[
                OrchestrationStage(
                    name="survey",
                    description="现状采集：兼容性 + 版本 + 日志",
                    tasks=[
                        _task("compatibility", 10, base),
                        _task("update_manager", 10, base),
                        _task("log_analyzer", 10, base, optional=True),
                    ],
                ),
                OrchestrationStage(
                    name="protect",
                    description="加固：安全巡检 → 存档备份 → Mod 同步",
                    dependencies=["survey"],
                    parallel=False,
                    tasks=[
                        _task("security_agent", 20, base, requires=["update_manager"]),
                        _task("save_manager", 30, base, requires=["security_agent"]),
                        _task("dlc_manager", 40, base, requires=["save_manager"]),
                    ],
                ),
                OrchestrationStage(
                    name="refresh",
                    description="刷新体验：启动优化（增益项）",
                    dependencies=["protect"],
                    tasks=[
                        _task("launch_optimizer", 50, base, optional=True),
                        _task("community_agent", 50, base, optional=True),
                    ],
                ),
            ],
        )


# --------------------------------------------------------------------------- #
# 工作流模板与意图分流
# --------------------------------------------------------------------------- #
# 方案三的核心：LLM（或用户）只在「入口」决定跑哪条工作流，
# 选定后的智能体链路由 OrchestrationStage / task.requires 锁死，不可被绕过。

# 意图关键词 → 工作流模板。正则先行，命中即停，免费且确定。
_INTENT_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("maintenance", (r"维护", r"巡检", r"体检", r"清理", r"备份存档", r"同步\s*mod",
                     r"更新一下", r"maintenance", r"checkup")),
    ("optimization", (r"卡", r"掉帧", r"帧率", r"fps", r"优化", r"性能", r"画质",
                      r"延迟", r"没声音", r"音效", r"卡顿", r"lag", r"stutter")),
    ("installation", (r"安装", r"装一下", r"装个", r"部署", r"新游戏", r"install",
                      r"下载并", r"完整流程", r"一条龙")),
    ("diagnostic", (r"报错", r"崩溃", r"闪退", r"打不开", r"启动不了", r"进不去",
                    r"error", r"crash", r"exception", r"失败", r"异常", r"排查",
                    r"诊断", r"解决")),
]

# 模板名 → 中文说明，供 CLI / GUI 展示可选项
WORKFLOW_LABELS: Dict[str, str] = {
    "installation": "安装全链路（采集 → 安装 → 调优 → 联机社交）",
    "diagnostic": "排障诊断（只读排查，不改动文件）",
    "optimization": "性能与体验调优",
    "maintenance": "日常维护（更新 / 备份 / Mod / 安全）",
}


def detect_intent(text: str) -> Optional[str]:
    """正则先行的意图分流：文本 → 工作流模板名。

    只做「跑哪条工作流」的粗分类，不做智能体级选择——后者由工作流模板
    的依赖关系确定性给出，LLM 无法插手，安全前置顺序因此有代码级保证。

    返回 ``None`` 表示无法判定，调用方应显式询问用户，而不是猜一个默认模板：
    选错工作流会执行到写操作（如 installation 会装 Mod），代价高于多问一句。
    """
    low = (text or "").lower()
    if not low.strip():
        return None
    for workflow, patterns in _INTENT_PATTERNS:
        if any(re.search(p, low) for p in patterns):
            return workflow
    return None


def build_workflow(orchestrator: GameAgentOrchestrator, workflow: str,
                   game_name: str, error_message: str = "",
                   game_dir: str = "") -> OrchestrationPlan:
    """按模板名生成工作流计划。

    :raises ValueError: 模板名未知时抛出，不静默回退——静默回退会让用户
        以为跑的是排障（只读），实际却跑了安装（含写操作）。
    """
    factory = WORKFLOW_TEMPLATES.get(workflow)
    if factory is None or isinstance(factory, str):
        known = "、".join(sorted(WORKFLOW_LABELS))
        raise ValueError(f"未知工作流模板：{workflow}（可选：{known}）")
    return factory(orchestrator, game_name, error_message, game_dir)


# 预定义的工作流模板：模板名 → GameAgentOrchestrator 上对应的工厂方法
WORKFLOW_TEMPLATES: Dict[str, Callable[..., OrchestrationPlan]] = {
    "diagnostic": GameAgentOrchestrator.create_diagnostic_workflow,
    "installation": GameAgentOrchestrator.create_installation_workflow,
    "optimization": GameAgentOrchestrator.create_optimization_workflow,
    "maintenance": GameAgentOrchestrator.create_maintenance_workflow,
}