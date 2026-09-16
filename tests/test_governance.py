"""制度化治理层单测（参照三省六部的分权协作）。

覆盖：
- 状态机：合法主链路、越级拦截、终态锁定、封驳回路与轮次计数、封还直办；
- 权限矩阵：协调角色白名单、调度官派发到未注册智能体被拒、执行层横向调用被拒；
- 审议官：未知智能体 / requires 缺失 / 阶段环 / 任务环 / 方案模式写操作封驳，
  安全前置软提示，执行结果验收复审；
- 规划官：模板兜底、LLM 动态 JSON 解析、坏输出降级 None；
- 协调器端到端：模板全链路、动态规划全链路、封驳后重拟通过、三轮硬伤终止、
  规划失败降级单智能体直答、方案模式写操作拦截。

异步统一用 ``asyncio.run()`` 包装，不引入 pytest-asyncio。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from gamedoctor.agents.base_agent import AgentResult, AgentTask, BaseAgent
from gamedoctor.governance.coordinator import GovernanceCoordinator
from gamedoctor.governance.permissions import Role, can_dispatch
from gamedoctor.governance.planner import Planner
from gamedoctor.governance.reviewer import Reviewer
from gamedoctor.governance.state_machine import (
    ALLOWED_TRANSITIONS,
    GovState,
    TaskTicket,
    TransitionError,
)
from gamedoctor.orchestrator.orchestrator import (
    GameAgentOrchestrator,
    OrchestrationPlan,
    OrchestrationStage,
    _task,
)


# --------------------------------------------------------------------------- #
# 测试桩
# --------------------------------------------------------------------------- #
class FakeAgent(BaseAgent):
    def _do_initialize(self) -> None:
        pass

    async def execute(self, task: AgentTask) -> AgentResult:
        return AgentResult(success=True, message=f"{task.name} ok")


class FakeSettings:
    def is_agent_enabled(self, name: str) -> bool:
        return True


class FakeManager:
    """最小 AgentManager：按名注册假智能体，编排器走真实 GameAgentOrchestrator。"""

    def __init__(self, names: List[str]):
        self.orchestrator = GameAgentOrchestrator()
        self.agents: Dict[str, BaseAgent] = {}
        for n in names:
            agent = FakeAgent(n)
            self.orchestrator.register_agents([agent])
            self.agents[n] = agent

    def list_agents(self) -> List[Dict[str, Any]]:
        return [{"name": n, "display_name": n.upper(), "capabilities": ["x"]}
                for n in self.agents]

    def get_agent(self, name: str) -> BaseAgent:
        return self.agents[name]

    def get_display_name(self, name: str) -> str:
        return name.upper()


class FakeClient:
    """按 system prompt 类型分流的假 LLM：规划调用走 plan_texts 队列，其余走回复/决策。"""

    def __init__(self, plan_texts: List[str] | None = None,
                 reply_text: str = "回奏：全部处理完成。",
                 decision: str = '{"agent": null, "reply": "这是直答内容。"}'):
        self.plan_texts = plan_texts or []
        self.reply_text = reply_text
        self.decision = decision
        self.plan_calls = 0

    def complete_messages(self, messages, model=None, temperature=None, max_tokens=None):
        system = messages[0]["content"]
        if "规划官" in system:
            i = min(self.plan_calls, len(self.plan_texts) - 1)
            text = self.plan_texts[i]
            self.plan_calls += 1
            return text
        if "JSON 对象" in system:
            return self.decision
        return self.reply_text


_GOOD_PLAN = """```json
{"goal": "两任务测试计划",
 "stages": [
   {"name": "collect", "description": "采集", "parallel": true, "dependencies": [],
    "tasks": [
      {"name": "t1", "agent": "a", "priority": 10, "requires": [], "optional": false},
      {"name": "t2", "agent": "b", "priority": 20, "requires": [], "optional": false}
    ]}
 ]}
```"""

_BAD_PLAN_GHOST = """{"goal": "引用不存在的智能体",
 "stages": [{"name": "s1", "tasks": [{"name": "x", "agent": "ghost", "priority": 10}]}]}"""


def _run(coro):
    return asyncio.run(coro)


def _coord(names: List[str]) -> tuple[GovernanceCoordinator, FakeManager]:
    manager = FakeManager(names)
    return GovernanceCoordinator(manager, FakeSettings()), manager


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #
class TestStateMachine:
    def test_happy_path(self):
        t = TaskTicket(title="x")
        seq = [
            (GovState.PLANNING, "接待官", "规划官"),
            (GovState.REVIEW, "规划官", "审议官"),
            (GovState.ASSIGNED, "审议官", "调度官"),
            (GovState.DOING, "调度官", "执行智能体"),
            (GovState.VERIFICATION, "执行智能体", "审议官"),
            (GovState.DONE, "审议官", "接待官"),
        ]
        for to, fr, tr in seq:
            t.transition(to, fr, tr, "测试流转")
        assert t.state == GovState.DONE
        assert t.is_terminal
        # flow_log 完整记录每一跳（含 Intake 建票后的 6 次转移）
        assert [e.to_role for e in t.flow_log] == [s[2] for s in seq]

    def test_intake_skips_to_done_for_chitchat(self):
        t = TaskTicket(title="你好")
        t.transition(GovState.DONE, "接待官", "接待官", "闲聊直答")
        assert t.state == GovState.DONE

    def test_illegal_skip_raises(self):
        t = TaskTicket(title="x")
        with pytest.raises(TransitionError):
            t.transition(GovState.ASSIGNED, "接待官", "调度官", "越级派发")

    def test_terminal_state_locked(self):
        t = TaskTicket(title="x")
        t.transition(GovState.CANCELLED, "接待官", "—", "取消")
        with pytest.raises(TransitionError):
            t.transition(GovState.PLANNING, "接待官", "规划官", "终态再改")

    def test_rebut_loop_counts_rounds(self):
        t = TaskTicket(title="x")
        t.transition(GovState.PLANNING, "接待官", "规划官", "")
        t.transition(GovState.REVIEW, "规划官", "审议官", "")
        assert t.review_round == 1
        t.transition(GovState.PLANNING, "审议官", "规划官", "封驳")
        t.transition(GovState.REVIEW, "规划官", "审议官", "")
        assert t.review_round == 2
        # 封驳回路合法且终态仍可准奏
        t.transition(GovState.ASSIGNED, "审议官", "调度官", "准奏")
        assert t.state == GovState.ASSIGNED

    def test_planner_return_to_intake(self):
        """规划官封还：散点问答无需立项，退接待官直办。"""
        t = TaskTicket(title="x")
        t.transition(GovState.PLANNING, "接待官", "规划官", "")
        t.transition(GovState.INTAKE, "规划官", "接待官", "封还")
        assert t.state == GovState.INTAKE
        t.transition(GovState.DONE, "接待官", "接待官", "直办完成")
        assert t.state == GovState.DONE

    def test_every_nonterminal_has_exit(self):
        """制度完备性：除终态外每个状态至少有一条出路。"""
        for state, exits in ALLOWED_TRANSITIONS.items():
            if state in (GovState.DONE, GovState.CANCELLED):
                assert exits == frozenset()
            else:
                assert exits


# --------------------------------------------------------------------------- #
# 权限矩阵
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "fr,to,known,expect_ok",
    [
        (Role.INTAKE, "planner", set(), True),
        (Role.INTAKE, "dispatcher", set(), False),       # 接待官不能直派
        (Role.PLANNER, "reviewer", set(), True),
        (Role.PLANNER, "dispatcher", set(), False),       # 规划官不能绕审议
        (Role.REVIEWER, "planner", set(), True),          # 封驳
        (Role.REVIEWER, "dispatcher", set(), True),       # 准奏
        (Role.DISPATCHER, "a", {"a", "b"}, True),
        (Role.DISPATCHER, "ghost", {"a", "b"}, False),    # 未注册智能体
        (Role.EXECUTOR, "b", {"a", "b"}, False),          # 执行层横向调用被禁
        (Role.EXECUTOR, "dispatcher", {"a"}, True),       # 执行层只能回报
        (Role.DISPATCHER, "planner", {"a"}, False),       # 调度官不能干预规划
    ],
)
def test_permission_matrix(fr, to, known, expect_ok):
    ok, reason = can_dispatch(fr, to, known)
    assert ok is expect_ok, f"{fr}->{to}: {reason}"


# --------------------------------------------------------------------------- #
# 审议官
# --------------------------------------------------------------------------- #
def _plan(stages: List[OrchestrationStage]) -> OrchestrationPlan:
    return OrchestrationPlan(name="t", description="t", stages=stages)


class TestReviewer:
    KNOWN = frozenset({
        "a", "b", "c", "game_content", "perf_security",
        "installation", "script_editor",
    })

    def test_valid_plan_approved(self):
        plan = _plan([OrchestrationStage("s1", [
            _task("a", 10, {}),
            _task("b", 20, {}, requires=["a"]),
        ])])
        v = Reviewer().review(plan, mode="edit", known_agents=self.KNOWN)
        assert v.approved, v.blocking

    def test_unknown_agent_blocked(self):
        plan = _plan([OrchestrationStage("s1", [_task("ghost", 10, {})])])
        v = Reviewer().review(plan, mode="edit", known_agents=self.KNOWN)
        assert not v.approved
        assert any("未注册或已停用" in b for b in v.blocking)

    def test_missing_requires_blocked(self):
        plan = _plan([OrchestrationStage(
            "s1", [_task("a", 10, {}, requires=["nope"], name="t1")])])
        v = Reviewer().review(plan, mode="edit", known_agents=self.KNOWN)
        assert not v.approved
        assert any("requires 了计划内不存在" in b for b in v.blocking)

    def test_task_cycle_blocked(self):
        plan = _plan([OrchestrationStage("s1", [
            _task("a", 10, {}, requires=["b"], name="a"),
            _task("b", 20, {}, requires=["a"], name="b"),
        ])])
        v = Reviewer().review(plan, mode="edit", known_agents=self.KNOWN)
        assert not v.approved
        assert any("依赖存在环" in b for b in v.blocking)

    def test_stage_cycle_blocked(self):
        s1 = OrchestrationStage("s1", [_task("a", 10, {})], dependencies=["s2"])
        s2 = OrchestrationStage("s2", [_task("b", 10, {})], dependencies=["s1"])
        v = Reviewer().review(_plan([s1, s2]), mode="edit", known_agents=self.KNOWN)
        assert not v.approved
        assert any("阶段依赖存在环" in b for b in v.blocking)

    def test_empty_stage_blocked(self):
        v = Reviewer().review(_plan([OrchestrationStage("s1", [])]),
                              mode="edit", known_agents=self.KNOWN)
        assert not v.approved

    def test_plan_mode_blocks_mutating_agents(self):
        plan = _plan([OrchestrationStage("s1", [_task("game_content", 10, {})])])
        v = Reviewer().review(plan, mode="plan", known_agents=self.KNOWN)
        assert not v.approved
        assert any("方案模式禁止写操作" in b for b in v.blocking)

    def test_plan_mode_allows_readonly(self):
        plan = _plan([OrchestrationStage("s1", [_task("a", 10, {})])])
        v = Reviewer().review(plan, mode="plan", known_agents=self.KNOWN)
        assert v.approved

    def test_dlc_without_security_is_warning_not_block(self):
        # 合并后：计划含 game_content（Mod 操作后果方）但缺 perf_security 前置巡检
        plan = _plan([OrchestrationStage(
            "s1", [_task("game_content", 10, {})])])
        v = Reviewer().review(plan, mode="edit", known_agents=self.KNOWN)
        assert v.approved  # 软提示不封驳
        assert any("反作弊" in w for w in v.warnings)

    def test_review_outcome_reports_failures(self):
        v = Reviewer().review_outcome({"counts": {"failed": 2, "skipped": 1}})
        assert v.approved  # 执行失败不要求重规划
        assert len(v.warnings) == 2


# --------------------------------------------------------------------------- #
# 规划官
# --------------------------------------------------------------------------- #
class TestPlanner:
    def _planner(self) -> Planner:
        return Planner(FakeManager(["a", "b"]).orchestrator)

    def test_template_takes_precedence(self):
        async def case():
            # 即使提供了 llm_call，命中模板也不调用 LLM
            async def boom(system, user):
                raise AssertionError("模板路径不应调用 LLM")
            r = await self._planner().draft(
                intent="diagnostic", message="游戏报错闪退", game_name="G",
                agents_desc="- a", llm_call=boom,
            )
            return r
        r = _run(case())
        assert r.source == "template"
        assert r.plan is not None and len(r.plan.stages) >= 2

    def test_dynamic_llm_plan_parsed(self):
        async def case():
            async def llm_call(system, user):
                assert "规划官" in system
                return _GOOD_PLAN
            r = await self._planner().draft(
                intent=None, message="帮我边查日志边看兼容性", game_name="G",
                agents_desc="- a\n- b", llm_call=llm_call,
            )
            return r
        r = _run(case())
        assert r.source == "llm"
        assert len(r.plan.stages[0].tasks) == 2
        assert {t.agent for t in r.plan.stages[0].tasks} == {"a", "b"}

    def test_bad_llm_output_degrades_to_none(self):
        async def case():
            async def llm_call(system, user):
                return "我无法规划这个，抱歉"
            r = await self._planner().draft(
                intent=None, message="随便聊聊", game_name="G",
                agents_desc="- a", llm_call=llm_call,
            )
            return r
        r = _run(case())
        assert r.source == "none" and r.plan is None

    def test_feedback_passed_to_llm(self):
        seen = {}

        async def case():
            async def llm_call(system, user):
                seen["user"] = user
                return _GOOD_PLAN
            await self._planner().draft(
                intent=None, message="x", game_name="G", agents_desc="- a\n- b",
                llm_call=llm_call, feedback=["智能体 ghost 不存在"],
            )
        _run(case())
        assert "封驳" in seen["user"] and "ghost" in seen["user"]


# --------------------------------------------------------------------------- #
# 协调器端到端
# --------------------------------------------------------------------------- #
class TestCoordinator:
    def test_dynamic_plan_full_chain(self):
        coord, _ = _coord(["a", "b"])
        client = FakeClient(plan_texts=[_GOOD_PLAN])
        out = _run(coord.run(
            "帮我做一次两阶段调查", client=client, model="m",
            agents_desc="- a\n- b", known_agents={"a", "b"},
        ))
        assert out["ticket"]["state"] == "Done"
        assert out["ticket"]["plan_source"] == "llm"
        assert out["ticket"]["review_rounds"] == 1
        # 完整制度链路出现在 flow_log
        roles = [(e["from"], e["to"]) for e in out["ticket"]["flow_log"]]
        assert ("接待官", "规划官") in roles
        assert ("审议官", "调度官") in roles
        assert ("执行智能体集群", "审议官") in roles
        assert out["result"]["counts"]["succeeded"] == 2
        assert "[审议] 第 1 轮准奏" in out["thinking"]

    def test_template_full_chain_diagnostic(self):
        # 合并后诊断模板：collect(compatibility/log_analyzer) →
        # inspect(perf_security 体检 + network_expert) → community_agent
        names = ["compatibility", "log_analyzer", "perf_security",
                 "network_expert", "community_agent"]
        coord, _ = _coord(names)
        client = FakeClient()  # 无 plan_texts：模板路径不应发生规划调用
        out = _run(coord.run(
            "游戏报错闪退", client=client, model="m",
            agents_desc="", known_agents=set(names),
        ))
        assert client.plan_calls == 0
        assert out["ticket"]["state"] == "Done"
        assert out["ticket"]["intent"] == "diagnostic"
        assert out["ticket"]["plan_source"] == "template"
        assert out["result"]["counts"]["succeeded"] >= 5

    def test_rebut_then_approve(self):
        coord, _ = _coord(["a", "b"])
        client = FakeClient(plan_texts=[_BAD_PLAN_GHOST, _GOOD_PLAN])
        out = _run(coord.run(
            "复杂需求", client=client, model="m",
            agents_desc="- a\n- b", known_agents={"a", "b"},
        ))
        assert client.plan_calls == 2
        assert out["ticket"]["state"] == "Done"
        assert out["ticket"]["review_rounds"] == 2
        assert any("封驳" in t for t in out["thinking"])

    def test_three_rounds_hard_blocking_terminates(self):
        coord, _ = _coord(["a", "b"])
        client = FakeClient(plan_texts=[_BAD_PLAN_GHOST] * 3)
        out = _run(coord.run(
            "复杂需求", client=client, model="m",
            agents_desc="- a\n- b", known_agents={"a", "b"},
        ))
        assert client.plan_calls == 3
        assert out["ticket"]["state"] == "Cancelled"
        assert out.get("error") == "review_rejected"
        assert "三轮审查" in out["reply"]

    def test_planning_failure_falls_back_to_direct_answer(self):
        coord, _ = _coord(["a", "b"])
        client = FakeClient(plan_texts=["无法解析的输出"])  # 规划失败
        out = _run(coord.run(
            "今天天气怎么样", client=client, model="m",
            agents_desc="- a", known_agents={"a", "b"},
        ))
        assert out["ticket"]["state"] == "Done"
        assert "这是直答内容" in out["reply"]
        # flow 中应有「规划官封还」一跳
        assert any(e["from"] == "规划官" and e["to"] == "接待官"
                   for e in out["ticket"]["flow_log"])

    def test_direct_single_agent_full_chain(self):
        """规划封还后接待官直办、调用单个智能体：须补走制度链路到 Done（回归 Intake→Doing 越级 500）。"""
        coord, _ = _coord(["a", "b"])
        client = FakeClient(
            plan_texts=["无法解析的输出"],  # 动态规划失败 → 封还接待官
            decision='{"agent": "a", "question": "列一下目录", "reason": "需要 a"}',
        )
        out = _run(coord.run(
            "帮我列一下这个目录里的文件", client=client, model="m",
            agents_desc="- a\n- b", known_agents={"a", "b"},
        ))
        assert out["ticket"]["state"] == "Done"
        assert out["agent_used"] == "a"
        assert out["ticket"]["plan_source"] == "single"
        # 直办也必须逐跳合法：补立项 → 审议 → 派发 → 执行 → 验收
        roles = [(e["from"], e["to"]) for e in out["ticket"]["flow_log"]]
        assert ("接待官", "规划官") in roles   # 封还后的单任务补立项
        assert ("审议官", "调度官") in roles
        assert ("调度官", "智能体「a」") in roles
        assert ("智能体「a」", "审议官") in roles
        assert ("审议官", "接待官") in roles

    def test_direct_single_agent_exception_cancelled_not_raised(self):
        """直办执行层抛异常时票据转 Cancelled 并正常回复，不冒泡成 HTTP 500。"""

        class RaisingAgent(FakeAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                raise RuntimeError("boom")

        manager = FakeManager(["a"])
        manager.agents["a"] = RaisingAgent("a")
        manager.orchestrator.register_agents([manager.agents["a"]])
        coord = GovernanceCoordinator(manager, FakeSettings())
        client = FakeClient(
            plan_texts=["无法解析的输出"],
            decision='{"agent": "a", "question": "x", "reason": "需要 a"}',
        )
        out = _run(coord.run(
            "随便做点什么", client=client, model="m",
            agents_desc="- a", known_agents={"a"},
        ))
        assert out["ticket"]["state"] == "Cancelled"
        assert "boom" in out["reply"]

    def test_plan_mode_blocks_mutating_intent(self):
        # 安装模板合并后引用的执行层全集
        names = ["compatibility", "log_analyzer", "perf_security",
                 "network_expert", "community_agent",
                 "installation", "game_content",
                 "audio_expert", "launch_optimizer", "social_agent"]
        coord, _ = _coord(names)
        client = FakeClient()
        out = _run(coord.run(
            "帮我安装这个游戏", client=client, model="m",
            agents_desc="", known_agents=set(names), mode="plan",
        ))
        assert client.plan_calls == 0
        assert out["ticket"]["state"] == "Cancelled"
        assert "方案模式" in out["reply"]

    def test_template_rejected_when_required_agent_disabled(self):
        # 固定模板引用的 perf_security 不在白名单（被用户停用）：审议封驳即终止
        names = ["compatibility", "log_analyzer",
                 "network_expert", "community_agent"]
        coord, _ = _coord(names + ["perf_security"])  # 执行层已注册，但白名单不含
        client = FakeClient()
        out = _run(coord.run(
            "游戏报错闪退", client=client, model="m",
            agents_desc="", known_agents=set(names),
        ))
        assert out["ticket"]["state"] == "Cancelled"
        assert out.get("error") == "review_rejected"
        assert any("perf_security" in t for t in out["thinking"])
