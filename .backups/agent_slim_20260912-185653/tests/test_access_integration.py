"""访问模式编排集成测试（TR-4.1~4.6）。

覆盖：沙箱票据的任务授权门（批准/拒绝/超时）、direct 零授权、plan×sandbox
四格矩阵、ChatRequest 校验、直办路径过门与会话注入、tracker 五类事件。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from gamedoctor.agents.base_agent import AgentResult, AgentTask, BaseAgent
from gamedoctor.governance.coordinator import GovernanceCoordinator
from gamedoctor.orchestrator.orchestrator import GameAgentOrchestrator
from gamedoctor.sandbox.context import AccessContext
from gamedoctor.sandbox.gate import ApprovalGate, get_gate, reset_gate
from gamedoctor.runtime import get_tracker

# 复用治理层测试桩
from tests.test_governance import (  # noqa: E402
    FakeClient,
    FakeManager,
    FakeSettings,
    _GOOD_PLAN,
)


_PLAN_WITH_REQUIRES = """```json
{"goal": "带依赖的两任务计划",
 "stages": [
   {"name": "collect", "description": "采集", "parallel": false, "dependencies": [],
    "tasks": [
      {"name": "t1", "agent": "a", "priority": 10, "requires": [], "optional": false},
      {"name": "t2", "agent": "b", "priority": 20, "requires": ["t1"], "optional": false}
    ]}
 ]}
```"""


class RecordingAgent(BaseAgent):
    """记录执行次数与注入的沙箱会话。"""

    def __init__(self, name: str, store: Dict[str, Any]):
        super().__init__(name)
        self.store = store

    def _do_initialize(self) -> None:
        pass

    async def execute(self, task: AgentTask) -> AgentResult:
        self.store["runs"] = self.store.get("runs", 0) + 1
        self.store["session"] = (task.data or {}).get("_sandbox_session")
        return AgentResult(success=True, message=f"{task.name} ok")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    apphome = tmp_path / "apphome"
    monkeypatch.setattr("gamedoctor.sandbox.session.app_dir", lambda: apphome)
    monkeypatch.setattr("gamedoctor.governance.coordinator.app_dir", apphome)
    reset_gate()
    get_tracker()._events.clear()

    store: Dict[str, Any] = {}
    manager = FakeManager.__new__(FakeManager)
    manager.orchestrator = GameAgentOrchestrator()
    manager.agents = {}
    for n in ("a", "b"):
        agent = RecordingAgent(n, store)
        manager.orchestrator.register_agents([agent])
        manager.agents[n] = agent
    coord = GovernanceCoordinator(manager, FakeSettings())

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    yield SimpleNamespace(coord=coord, manager=manager, store=store, game_dir=game_dir)
    reset_gate()


async def _drive(coro, gate: ApprovalGate, approve: bool):
    """运行协调器协程，同时对新出现的授权请求逐条批准/拒绝；返回(结果, 请求列表)。"""
    task = asyncio.ensure_future(coro)
    seen = set()
    requests: List[Any] = []
    while not task.done():
        for req in gate.list_pending():
            if req.id not in seen:
                seen.add(req.id)
                requests.append(req)
                gate.decide(req.id, approve, "测试批准" if approve else "测试拒绝")
        await asyncio.sleep(0.01)
    return task.result(), requests


# --------------------------------------------------------------------- #
# TR-4.1 沙箱票据：授权请求数与任务数一致；批准全过 / 拒绝全跳过
# --------------------------------------------------------------------- #
def test_sandbox_workflow_approved(env) -> None:
    client = FakeClient(plan_texts=[_GOOD_PLAN])
    gate = get_gate()
    out, reqs = asyncio.run(_drive(env.coord.run(
        "帮我做一次两阶段调查", client=client, model="m",
        agents_desc="- a\n- b", known_agents={"a", "b"},
        game_dir=str(env.game_dir), access_mode="sandbox",
    ), gate, True))

    assert out["ticket"]["state"] == "Done"
    assert len(reqs) == 2
    assert {r.agent for r in reqs} == {"a", "b"}
    assert all(r.kind == "read" for r in reqs)
    assert all(r.ticket_id == out["ticket"]["id"] for r in reqs)
    assert out["result"]["counts"]["succeeded"] == 2
    # 会话已注入且封存待审
    assert env.store["session"] is not None
    assert out["sandbox"] is not None
    assert out["sandbox"]["status"] == "ready"
    assert out["sandbox"]["ticket_id"] == out["ticket"]["id"]
    assert "【沙箱授权】" in out["reply"]


def test_sandbox_workflow_rejected_skips_all(env) -> None:
    client = FakeClient(plan_texts=[_PLAN_WITH_REQUIRES])
    gate = get_gate()
    out, reqs = asyncio.run(_drive(env.coord.run(
        "复杂需求", client=client, model="m",
        agents_desc="- a\n- b", known_agents={"a", "b"},
        game_dir=str(env.game_dir), access_mode="sandbox",
    ), gate, False))

    assert out["ticket"]["state"] == "Done"          # 无非法转移
    assert len(reqs) == 1                            # t1 被拒，t2 被 requires 门控跳过
    counts = out["result"]["counts"]
    assert counts["skipped"] == 2
    assert counts["failed"] == 0
    assert out["result"]["success"] is True
    assert env.store.get("runs", 0) == 0             # 智能体完全未执行
    assert out["sandbox"]["change_count"] == 0


def test_sandbox_timeout_rejects_when_idle(env, monkeypatch: pytest.MonkeyPatch) -> None:
    short_gate = ApprovalGate(ttl=0.05)
    monkeypatch.setattr("gamedoctor.sandbox.gate.get_gate", lambda: short_gate)
    client = FakeClient(plan_texts=[_GOOD_PLAN])

    async def case():
        # 无人裁决 → 0.05s 超时自动拒绝
        return await env.coord.run(
            "复杂需求", client=client, model="m",
            agents_desc="- a\n- b", known_agents={"a", "b"},
            game_dir=str(env.game_dir), access_mode="sandbox",
        )

    out = asyncio.run(asyncio.wait_for(case(), timeout=5))
    assert out["ticket"]["state"] == "Done"
    assert out["result"]["counts"]["skipped"] == 2


# --------------------------------------------------------------------- #
# TR-4.2 direct 模式：零授权、不建会话，行为与现状一致
# --------------------------------------------------------------------- #
def test_direct_mode_no_gate_no_session(env) -> None:
    gate = get_gate()
    client = FakeClient(plan_texts=[_GOOD_PLAN])
    out, reqs = asyncio.run(_drive(env.coord.run(
        "帮我做一次两阶段调查", client=client, model="m",
        agents_desc="- a\n- b", known_agents={"a", "b"},
        game_dir=str(env.game_dir), access_mode="direct",
    ), gate, True))

    assert out["ticket"]["state"] == "Done"
    assert reqs == []
    assert gate.list_pending() == []
    assert out["sandbox"] is None
    assert env.store.get("session") is None
    assert out["result"]["counts"]["succeeded"] == 2


def test_invalid_access_mode_raises(env) -> None:
    client = FakeClient(plan_texts=[_GOOD_PLAN])
    with pytest.raises(ValueError):
        asyncio.run(env.coord.run(
            "x", client=client, model="m", agents_desc="- a",
            known_agents={"a"}, access_mode="root",
        ))


# --------------------------------------------------------------------- #
# TR-4.3 模式四格矩阵：plan 模式写操作在两种访问模式下都被封驳
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("access_mode", ["direct", "sandbox"])
def test_plan_mode_blocks_mutating_in_both_access_modes(env, access_mode: str) -> None:
    names = ["compatibility", "log_analyzer", "security_agent",
             "network_expert", "performance_analyst", "community_agent",
             "installation", "save_manager", "dlc_manager", "update_manager"]
    manager = FakeManager(names)
    coord = GovernanceCoordinator(manager, FakeSettings())
    client = FakeClient()
    out = asyncio.run(coord.run(
        "帮我安装这个游戏", client=client, model="m",
        agents_desc="", known_agents=set(names),
        mode="plan", access_mode=access_mode,
    ))
    assert out["ticket"]["state"] == "Cancelled"
    assert "方案模式" in out["reply"]
    assert out["sandbox"] is None  # 立项前拦截，不创建会话


def test_classify_kinds() -> None:
    classify = AccessContext.classify
    assert classify("compatibility", {}) == "read"
    assert classify("script_editor", {"operation": "edit"}) == "write"
    assert classify("script_editor", {"operation": "read"}) == "read"
    assert classify("save_manager", {}) == "write"
    assert classify("web_search", {"operation": "download", "url": "http://x"}) == "download"
    assert classify("web_search", {"url": "http://x/f.bin"}) == "download"
    assert classify("installation", {}) == "install"


# --------------------------------------------------------------------- #
# TR-4.4 ChatRequest：默认 sandbox、非法值 4xx
# --------------------------------------------------------------------- #
def test_chat_request_validation() -> None:
    from fastapi.testclient import TestClient

    from gamedoctor.server import ChatRequest, app

    assert ChatRequest(message="x").access_mode == "sandbox"

    client = TestClient(app)
    resp = client.post("/chat", json={"message": "你好", "access_mode": "bogus"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_access_mode"

    resp = client.post("/chat", json={"message": "  "})
    assert resp.status_code == 200
    assert resp.json()["sandbox"] is None


# --------------------------------------------------------------------- #
# TR-4.5 直办路径沙箱过门与会话注入（回归直办 500 链路）
# --------------------------------------------------------------------- #
def test_single_direct_path_sandbox_gated(env) -> None:
    client = FakeClient(
        plan_texts=["无法解析的输出"],
        decision='{"agent": "a", "question": "列一下目录", "reason": "需要 a"}',
    )
    gate = get_gate()
    out, reqs = asyncio.run(_drive(env.coord.run(
        "帮我列一下这个目录里的文件", client=client, model="m",
        agents_desc="- a\n- b", known_agents={"a", "b"},
        game_dir=str(env.game_dir), access_mode="sandbox",
    ), gate, True))

    assert out["ticket"]["state"] == "Done"
    assert out["agent_used"] == "a"
    assert len(reqs) == 1 and reqs[0].agent == "a"
    assert env.store.get("session") is not None
    assert out["sandbox"] is not None and out["sandbox"]["status"] == "ready"


# --------------------------------------------------------------------- #
# TR-4.6 tracker 五类事件
# --------------------------------------------------------------------- #
def test_tracker_events_cover_five_kinds(env) -> None:
    # 批准链路：等待授权 / 已获授权 / 沙箱提示
    gate = get_gate()
    client = FakeClient(plan_texts=[_GOOD_PLAN])
    asyncio.run(_drive(env.coord.run(
        "帮我做一次两阶段调查", client=client, model="m",
        agents_desc="- a\n- b", known_agents={"a", "b"},
        game_dir=str(env.game_dir), access_mode="sandbox",
    ), gate, True))
    text = "\n".join(e["message"] for e in get_tracker().snapshot()["events"])
    assert "等待授权" in text
    assert "已获授权" in text
    assert "沙箱模式" in text

    # 拒绝链路：跳过事件
    get_tracker()._events.clear()
    client2 = FakeClient(plan_texts=[_GOOD_PLAN])
    gate2 = get_gate()
    asyncio.run(_drive(env.coord.run(
        "帮我做一次两阶段调查", client=client2, model="m",
        agents_desc="- a\n- b", known_agents={"a", "b"},
        game_dir=str(env.game_dir), access_mode="sandbox",
    ), gate2, False))
    text = "\n".join(e["message"] for e in get_tracker().snapshot()["events"])
    assert "任务跳过" in text

    # direct 链路：跳过任务授权提示
    get_tracker()._events.clear()
    client3 = FakeClient(plan_texts=[_GOOD_PLAN])
    asyncio.run(env.coord.run(
        "帮我做一次两阶段调查", client=client3, model="m",
        agents_desc="- a\n- b", known_agents={"a", "b"},
        game_dir=str(env.game_dir), access_mode="direct",
    ))
    text = "\n".join(e["message"] for e in get_tracker().snapshot()["events"])
    assert "直接访问模式，跳过任务授权" in text
