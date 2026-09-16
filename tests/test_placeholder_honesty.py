"""批6：占位智能体诚实化 + AgentResult.unverified + 审议官验收规则。"""

from __future__ import annotations

import asyncio
import json

from gamedoctor.agents.audio.audio_expert_agent import AudioExpertAgent
from gamedoctor.agents.base_agent import AgentResult, AgentTask
from gamedoctor.agents.community.community_agent import CommunityAgent
from gamedoctor.agents.network.network_expert_agent import NetworkExpertAgent
from gamedoctor.agents.social.social_agent import SocialAgent
from gamedoctor.governance.coordinator import _workflow_summary_json
from gamedoctor.governance.reviewer import Reviewer


def _run(agent, data=None):
    task = AgentTask(name="t", data=data or {"game_name": "示例游戏"})
    return asyncio.run(agent.execute(task))


# --------------------------------------------------------------------------- #
# 四个占位智能体：不得返回虚构数据，必须显式申报 unverified
# --------------------------------------------------------------------------- #
def test_audio_agent_is_honest_placeholder():
    res = _run(AudioExpertAgent())
    assert res.success
    assert res.unverified, "音频能力未接入必须申报未验证项"
    assert "尚未接入" in res.message
    d = res.data
    assert d.detected_devices == []
    assert d.format_issues == []
    assert d.problems == []
    assert d.recommendations == []
    assert d.spatial_audio_enabled is False


def test_social_agent_is_honest_placeholder():
    res = _run(SocialAgent())
    assert res.success
    assert res.unverified
    assert "尚未接入" in res.message
    d = res.data
    assert d.friends_online == []
    assert d.voice_channels == []
    assert d.linked_accounts == []
    assert d.privacy_settings == {}
    assert d.recommendations == []


def test_community_agent_is_honest_placeholder():
    res = _run(CommunityAgent())
    assert res.success
    assert res.unverified
    assert "尚未接入" in res.message
    d = res.data
    assert d.tutorials == []
    assert d.videos == []
    assert d.discussions == []
    assert d.recommendations == []


def test_network_agent_default_is_honest_placeholder(monkeypatch):
    agent = NetworkExpertAgent()
    monkeypatch.setattr(agent, "_ping", lambda host: None)  # 隔离真实网络
    res = _run(agent, {"game_name": "某联机游戏"})
    assert res.success
    d = res.data
    assert d.latency_ms is None
    assert d.nat_type == "unknown"
    assert d.recommended_ports == [], "未配置端口表时不得返回写死的常见端口"
    assert d.recommended_servers == []
    assert d.issues == ["无法建立到目标主机的连接"]
    joined = "、".join(res.unverified)
    assert "NAT" in joined
    assert "端口" in joined
    assert "游戏服务器连通性" in joined
    recs = "；".join(d.recommendations)
    assert "已开启路由优化" not in recs  # 假声明不得再现


def test_network_agent_ports_from_config_and_custom_host(monkeypatch):
    agent = NetworkExpertAgent(config={
        "game_ports": {"某联机游戏": {"udp": [27015], "tcp": [27016]}},
    })
    monkeypatch.setattr(agent, "_ping", lambda host: 23.5)

    res = _run(agent, {"game_name": "某联机游戏", "host": "mc.example.com"})
    d = res.data
    assert d.latency_ms == 23.5
    assert d.recommended_ports == [27015, 27016]
    assert d.issues == []
    joined = "、".join(res.unverified)
    assert "游戏服务器连通性" not in joined, "显式给了游戏主机时不再申报该项"
    assert "端口表未配置" not in joined

    # data.ports 优先于配置
    agent2 = NetworkExpertAgent()
    monkeypatch.setattr(agent2, "_ping", lambda host: 10.0)
    res2 = _run(agent2, {"game_name": "X", "ports": {"tcp": [8080]}})
    assert res2.data.recommended_ports == [8080]


# --------------------------------------------------------------------------- #
# AgentResult.unverified 默认值
# --------------------------------------------------------------------------- #
def test_agent_result_unverified_defaults_empty():
    r = AgentResult(success=True)
    assert r.unverified == []
    r2 = AgentResult(success=True, unverified=["x"])
    assert r2.unverified == ["x"]


# --------------------------------------------------------------------------- #
# 审议官验收复审：汇总 unverified 申报
# --------------------------------------------------------------------------- #
def test_review_outcome_collects_unverified():
    outcome = {
        "counts": {"succeeded": 3, "failed": 0, "skipped": 0},
        "task_results": {
            "audio": AgentResult(success=True, unverified=["音频设备枚举"]),
            "install": AgentResult(success=True),
            "skipped_audio": AgentResult(success=True, skipped=True,
                                         unverified=["不应计数"]),
        },
    }
    v = Reviewer().review_outcome(outcome)
    assert v.approved  # 未验证项是带保留通过，不封驳
    assert any("audio" in w and "音频设备枚举" in w for w in v.warnings)
    assert all("skipped_audio" not in w for w in v.warnings)
    assert all("install" not in w for w in v.warnings)


def test_review_outcome_failed_and_skipped_counts():
    v = Reviewer().review_outcome({
        "counts": {"failed": 2, "skipped": 1},
        "task_results": {},
    })
    assert v.approved
    text = "；".join(v.warnings)
    assert "2 个必选任务执行失败" in text
    assert "1 个任务" in text


def test_workflow_summary_json_carries_unverified_and_warnings():
    outcome = {
        "plan_name": "p",
        "success": True,
        "counts": {"succeeded": 1},
        "stages": {
            "s1": {
                "audio": AgentResult(success=True, message="m",
                                     unverified=["音频设备枚举"]),
            }
        },
    }
    payload = json.loads(_workflow_summary_json(outcome, warnings=["审议警告 A"]))
    assert payload["review_warnings"] == ["审议警告 A"]
    assert payload["stages"]["s1"]["audio"]["unverified"] == ["音频设备枚举"]
