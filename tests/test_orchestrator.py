"""GameAgentOrchestrator 单测 — 方案三确定性主干。

覆盖：
- detect_intent 意图分流（四类命中 + 未命中 + 空输入）
- build_workflow 模板工厂（未知模板抛错、四模板阶段数）
- 阶段级依赖：前置阶段必选任务失败 → 后续阶段整段跳过
- 任务级门控：requires 失败只跳过该任务，同阶段兄弟任务照常执行
- optional 任务失败/跳过不计入整体成败
- 串行阶段按 (priority, name) 排序执行
- 并行阶段全部 runnable 任务执行、未满足依赖的跳过
- 结果计数（succeeded / failed / skipped / optional_failed）

异步执行统一用 ``asyncio.run()`` 包装，不引入 pytest-asyncio 依赖。
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from gamedoctor.agents.base_agent import AgentResult, AgentTask, BaseAgent
from gamedoctor.models import GameContext
from gamedoctor.orchestrator.orchestrator import (
    GameAgentOrchestrator,
    OrchestrationPlan,
    OrchestrationStage,
    build_workflow,
    detect_intent,
    _task,
)


class FakeAgent(BaseAgent):
    """测试用假智能体：按任务名决定成败，并记录执行顺序。"""

    def __init__(self, name: str, fail_tasks: set[str] | None = None,
                 order: List[str] | None = None):
        super().__init__(name)
        self.fail_tasks = fail_tasks or set()
        self.order = order

    def _do_initialize(self) -> None:
        pass

    async def execute(self, task: AgentTask) -> AgentResult:
        if self.order is not None:
            self.order.append(task.name)
        if task.name in self.fail_tasks:
            return AgentResult(success=False, message=f"{task.name} forced failure")
        return AgentResult(success=True, message=f"{task.name} ok")


def _orchestrator(fail_tasks: set[str] | None = None,
                  order: List[str] | None = None) -> GameAgentOrchestrator:
    """注册一批通用假智能体（名字 a-e）。"""
    orch = GameAgentOrchestrator()
    for name in ("a", "b", "c", "d", "e"):
        orch.register_agents([FakeAgent(name, fail_tasks=fail_tasks, order=order)])
    return orch


def _ctx() -> GameContext:
    return GameContext(game_name="TestGame")


def _run(orch: GameAgentOrchestrator, plan: OrchestrationPlan) -> dict:
    return asyncio.run(orch.execute_workflow(plan, _ctx()))


# --------------------------------------------------------------------------- #
# 意图分流
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("帮我安装这个游戏", "installation"),
        ("游戏装一下", "installation"),
        ("游戏报错闪退", "diagnostic"),
        ("启动不了怎么办", "diagnostic"),
        ("游戏卡顿掉帧", "optimization"),
        ("帧率太低优化一下", "optimization"),
        ("做个体检清理", "maintenance"),
        ("备份存档同步mod", "maintenance"),
        ("今天天气不错", None),
        ("", None),
        ("   ", None),
    ],
)
def test_detect_intent(text, expected):
    assert detect_intent(text) == expected


def test_detect_intent_pattern_priority():
    """同时命中多个模板时按 _INTENT_PATTERNS 声明顺序取先（maintenance 先于 optimization）。"""
    assert detect_intent("清理一下卡顿") == "maintenance"


# --------------------------------------------------------------------------- #
# 模板工厂
# --------------------------------------------------------------------------- #
def test_build_workflow_unknown_raises():
    orch = _orchestrator()
    with pytest.raises(ValueError, match="未知工作流模板"):
        build_workflow(orch, "nonexistent", "Game")


@pytest.mark.parametrize(
    "workflow,stage_count",
    [("installation", 4), ("diagnostic", 3), ("optimization", 3), ("maintenance", 3)],
)
def test_build_workflow_templates(workflow, stage_count):
    orch = _orchestrator()
    plan = build_workflow(orch, workflow, "TestGame", error_message="报错", game_dir="")
    assert len(plan.stages) == stage_count
    for stage in plan.stages:
        for task in stage.tasks:
            assert task.agent  # 所有任务都有显式路由目标


# --------------------------------------------------------------------------- #
# 阶段级依赖
# --------------------------------------------------------------------------- #
def test_stage_dependency_skip_on_failure():
    """前置阶段必选任务失败 → 依赖它的后续阶段整段跳过。"""
    orch = _orchestrator(fail_tasks={"a"})
    plan = OrchestrationPlan(
        name="t", description="t",
        stages=[
            OrchestrationStage(name="s1", tasks=[_task("a", 10, {})]),
            OrchestrationStage(name="s2", dependencies=["s1"],
                               tasks=[_task("b", 10, {})]),
        ],
    )
    out = _run(orch, plan)
    assert out["success"] is False
    assert out["task_results"]["a"].success is False
    assert out["task_results"]["b"].skipped is True  # s2 整段跳过
    assert out["counts"]["failed"] == 1
    assert out["counts"]["skipped"] == 1


def test_stage_dependency_optional_failure_does_not_block():
    """前置阶段只有 optional 任务失败 → 不阻断后续阶段。"""
    orch = _orchestrator(fail_tasks={"a"})
    plan = OrchestrationPlan(
        name="t", description="t",
        stages=[
            OrchestrationStage(name="s1", tasks=[
                _task("a", 10, {}, optional=True),
                _task("b", 10, {}),
            ]),
            OrchestrationStage(name="s2", dependencies=["s1"],
                               tasks=[_task("c", 10, {})]),
        ],
    )
    out = _run(orch, plan)
    assert out["success"] is True  # optional 失败不计
    assert out["task_results"]["c"].success is True
    assert out["counts"]["optional_failed"] == 1


# --------------------------------------------------------------------------- #
# 任务级门控
# --------------------------------------------------------------------------- #
def test_task_level_gating_sibling_runs():
    """串行阶段内 requires 失败只跳过该任务，兄弟任务照常执行。"""
    orch = _orchestrator(fail_tasks={"a"})
    plan = OrchestrationPlan(
        name="t", description="t",
        parallel_execution=False,
        stages=[
            OrchestrationStage(
                name="s1", parallel=False,
                tasks=[
                    _task("a", 10, {}),
                    _task("b", 20, {}, requires=["a"]),  # a 失败 → b 跳过
                    _task("c", 30, {}),                  # 无依赖 → 照常执行
                ],
            ),
        ],
    )
    out = _run(orch, plan)
    results = out["task_results"]
    assert results["a"].success is False
    assert results["b"].skipped is True
    assert results["c"].success is True


def test_parallel_stage_skips_unsatisfied():
    """并行阶段：未满足依赖的任务跳过，其余并行执行。"""
    order: List[str] = []
    orch = _orchestrator(order=order)
    plan = OrchestrationPlan(
        name="t", description="t",
        stages=[
            OrchestrationStage(
                name="s1", parallel=True,
                tasks=[
                    _task("a", 10, {}),
                    _task("b", 10, {}, requires=["ghost"]),  # 依赖不存在 → 跳过
                ],
            ),
        ],
    )
    out = _run(orch, plan)
    results = out["task_results"]
    assert results["a"].success is True
    assert results["b"].skipped is True
    assert "a" in order and "b" not in order


# --------------------------------------------------------------------------- #
# 串行排序
# --------------------------------------------------------------------------- #
def test_sequential_stage_priority_order():
    """串行阶段按 (priority, name) 升序执行，忽略模板声明顺序。"""
    order: List[str] = []
    orch = _orchestrator(order=order)
    plan = OrchestrationPlan(
        name="t", description="t",
        stages=[
            OrchestrationStage(
                name="s1", parallel=False,
                tasks=[
                    _task("c", 30, {}),
                    _task("a", 10, {}),
                    _task("b", 20, {}),
                ],
            ),
        ],
    )
    _run(orch, plan)
    assert order == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# 计数聚合
# --------------------------------------------------------------------------- #
def test_counts_aggregation():
    orch = _orchestrator(fail_tasks={"b"})
    plan = OrchestrationPlan(
        name="t", description="t",
        stages=[
            OrchestrationStage(
                name="s1", parallel=False,
                tasks=[
                    _task("a", 10, {}),
                    _task("b", 20, {}),
                    _task("c", 30, {}, requires=["b"]),      # 跳过
                    _task("d", 40, {}, optional=True),       # 成功的可选项
                ],
            ),
        ],
    )
    out = _run(orch, plan)
    counts = out["counts"]
    assert counts["total"] == 4
    assert counts["succeeded"] == 2   # a、d
    assert counts["failed"] == 1      # b（必选失败）
    assert counts["skipped"] == 1     # c
    assert out["failed_tasks"] == ["b"]
    assert out["success"] is False
