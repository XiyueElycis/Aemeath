"""智能体精简合并契约：perf_security / game_content 的 op 分流与注册收口。

历史背景：原 performance_analyst + security_agent 合并为 perf_security；
原 update_manager + save_manager + dlc_manager 合并为 game_content。
智能体总数 15 → 12，旧注册名不得残留。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from gamedoctor.agents.base_agent import AgentTask
from gamedoctor.agents.content.game_content_agent import GameContentAgent
from gamedoctor.agents.health.perf_security_agent import PerfSecurityAgent
from gamedoctor.orchestrator.agent_factory import (
    DEFAULT_AGENT_CONFIGS,
    AgentFactory,
    register_all_agents,
)


# --------------------------------------------------------------------- #
# 注册收口：15 → 12，旧名消失
# --------------------------------------------------------------------- #
def test_registry_collapsed_to_twelve():
    register_all_agents()
    names = {c["name"] for c in DEFAULT_AGENT_CONFIGS}
    assert len(names) == 12
    # 五个旧注册名全部消失
    assert not (names & {
        "performance_analyst", "security_agent",
        "save_manager", "update_manager", "dlc_manager",
    })
    # 两个新注册名存在
    assert {"perf_security", "game_content"} <= names
    # 工厂类型表同步收口
    assert AgentFactory.get_available_agents()  # 非空即可
    for gone in ("performance", "security", "save_manager", "update_manager", "dlc_manager"):
        # type 注册表是模块级 dict，直接断言不可创建
        with pytest.raises(ValueError):
            AgentFactory.create_agent(gone)
    # 新类型可创建
    assert AgentFactory.create_agent("perf_security").name == "perf_security"
    assert AgentFactory.create_agent("game_content").name == "game_content"


# --------------------------------------------------------------------- #
# perf_security 分流
# --------------------------------------------------------------------- #
def _perf_task(op: str = "", name: str = "perf_security", caps=None) -> AgentTask:
    data: dict = {"game_name": "G"}
    if op:
        data["op"] = op
    return AgentTask(name=name, agent="perf_security", data=data,
                     required_capabilities=list(caps or []))


def test_perf_security_op_routing():
    agent = PerfSecurityAgent()
    agent.initialize()

    # 显式 op
    r = asyncio.run(agent.execute(_perf_task("performance")))
    assert r.success and r.data.op == "performance"
    assert r.data.performance is not None and r.data.security is None

    r = asyncio.run(agent.execute(_perf_task("security")))
    assert r.success and r.data.op == "security"
    assert r.data.security is not None and r.data.performance is None

    # 缺省全检
    r = asyncio.run(agent.execute(_perf_task()))
    assert r.success and r.data.op == "health"
    assert r.data.performance is not None and r.data.security is not None

    # 任务名推断（工作流 perf_baseline / security_check）
    assert agent._infer_op(
        _perf_task(name="perf_baseline"), {"game_name": "G"}) == "performance"
    assert agent._infer_op(
        _perf_task(name="security_check"), {"game_name": "G"}) == "security"

    # 能力标签推断（REPL：/run 走 required_capabilities）
    assert agent._infer_op(
        _perf_task(caps=["security"]), {"game_name": "G"}) == "security"
    assert agent._infer_op(
        _perf_task(caps=["performance"]), {"game_name": "G"}) == "performance"

    # operation 字段（LLM 规划产出）与 op 等价
    t = AgentTask(name="t", agent="perf_security",
                  data={"game_name": "G", "operation": "security"})
    assert agent._infer_op(t, t.data) == "security"


# --------------------------------------------------------------------- #
# game_content 分流
# --------------------------------------------------------------------- #
def test_game_content_op_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # saves 备份会写 ~/.gamedoctor，重定向 home
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "slot1.sav").write_bytes(b"SAVE1")
    agent = GameContentAgent()
    agent.initialize()

    # version 只读子项
    t = AgentTask(name="update_management", agent="game_content",
                  data={"game_name": "G", "op": "version", "current_version": "1.0"})
    r = asyncio.run(agent.execute(t))
    assert r.success and r.data.op == "version"
    assert r.data.version is not None and r.data.saves is None and r.data.dlc is None
    assert r.data.version.current_version == "1.0"

    # saves 子项
    t = AgentTask(name="save_management", agent="game_content",
                  data={"game_name": "G", "op": "saves", "save_path": str(saves)})
    r = asyncio.run(agent.execute(t))
    assert r.success and r.data.op == "saves"
    assert r.data.saves is not None and len(r.data.saves.saves) == 1

    # dlc 只读子项
    t = AgentTask(name="dlc_management", agent="game_content",
                  data={"game_name": "G", "op": "dlc"})
    r = asyncio.run(agent.execute(t))
    assert r.success and r.data.op == "dlc"
    assert r.data.dlc is not None and r.data.version is None

    # 缺省 content：三块全做（顺序 version→saves→dlc）
    t = AgentTask(name="manual_game_content", agent="game_content",
                  data={"game_name": "G", "save_path": str(saves)})
    r = asyncio.run(agent.execute(t))
    assert r.success and r.data.op == "content"
    assert r.data.version is not None
    assert r.data.saves is not None
    assert r.data.dlc is not None

    # 能力标签推断（REPL 路由）
    assert agent._infer_op(
        AgentTask(name="t", agent="game_content", data={},
                  required_capabilities=["dlc_manager"]), {}) == "dlc"
    assert agent._infer_op(
        AgentTask(name="t", agent="game_content", data={},
                  required_capabilities=["save_manager"]), {}) == "saves"
    assert agent._infer_op(
        AgentTask(name="t", agent="game_content", data={},
                  required_capabilities=["update_manager"]), {}) == "version"
