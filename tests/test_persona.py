"""角色人格层（aemeath_agent.persona）+ 开关接线 单测。

覆盖：
- 人格资料与系统提示词渲染；
- apply_persona / decision_hint 的开与关；
- 回奏（generate_reply）与决策系统提示（_decision_system）两处注入点；
- Settings 人格开关的持久化。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from aemeath_agent import persona
from aemeath_agent.llm.agent_router import (
    MODE_COMPANION,
    AgentChatService,
    _decision_system,
    companion_chat,
    generate_reply,
)
from aemeath_agent.orchestrator.agent_factory import DEFAULT_AGENT_CONFIGS
from aemeath_agent.settings import Settings


class _CaptureClient:
    """记录消息与调用参数，不发起真实请求。"""

    def __init__(self) -> None:
        self.calls: List[List[Dict[str, Any]]] = []
        self.last_temperature = None

    def complete_messages(self, messages, model=None, temperature=None, max_tokens=None):
        self.calls.append(messages)
        self.last_temperature = temperature
        return "已生成"


# --------------------------------------------------------------------------- #
# 资料与渲染
# --------------------------------------------------------------------------- #


def test_active_persona_is_aemeath():
    p = persona.ACTIVE_PERSONA
    assert p.id == "aemeath"
    assert p.name == "爱弥斯"
    assert "鸣潮" in p.source


def test_persona_system_block_content():
    block = persona.persona_system_block()
    assert "爱弥斯" in block
    assert "电子幽灵" in block
    assert "拯救世界" in block
    # 边界必须强调技术结论不准编造
    assert "准确" in block


def test_apply_persona_enabled():
    out = persona.apply_persona("基础指令", enabled=True)
    assert out.startswith("【你的角色】")
    assert out.endswith("基础指令")


def test_apply_persona_disabled():
    assert persona.apply_persona("基础指令", enabled=False) == "基础指令"


def test_decision_hint_on_off():
    assert "爱弥斯" in persona.decision_hint(enabled=True)
    assert persona.decision_hint(enabled=False) == ""


# --------------------------------------------------------------------------- #
# 注入点
# --------------------------------------------------------------------------- #


def test_generate_reply_uses_persona(monkeypatch):
    monkeypatch.setattr(persona, "persona_enabled", lambda: True)
    client = _CaptureClient()

    generate_reply(client, "游戏打不开", "日志分析", '{"success": true}', None)

    system_msg = client.calls[0][0]
    assert system_msg["role"] == "system"
    assert "爱弥斯" in system_msg["content"]
    # 任务指令仍在，人格只加在前面
    assert "执行结果" in system_msg["content"]


def test_generate_reply_persona_off(monkeypatch):
    monkeypatch.setattr(persona, "persona_enabled", lambda: False)
    client = _CaptureClient()

    generate_reply(client, "游戏打不开", "日志分析", '{"success": true}', None)

    system_msg = client.calls[0][0]
    assert "【你的角色】" not in system_msg["content"]


def test_decision_system_persona_hint(monkeypatch):
    monkeypatch.setattr(persona, "persona_enabled", lambda: True)
    system = _decision_system("（清单）", "简短判断", "edit")
    assert "爱弥斯" in system
    # JSON 主体指令未被人格块替换
    assert "JSON" in system


def test_decision_system_persona_off(monkeypatch):
    monkeypatch.setattr(persona, "persona_enabled", lambda: False)
    system = _decision_system("（清单）", "简短判断", "edit")
    assert "电子幽灵" not in system


# --------------------------------------------------------------------------- #
# Settings 开关
# --------------------------------------------------------------------------- #


def test_settings_persona_default_on(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert s.is_persona_enabled() is True


def test_settings_persona_persisted(tmp_path):
    path = tmp_path / "settings.json"
    Settings(path).set_persona_enabled(False)
    assert Settings(path).is_persona_enabled() is False

    Settings(path).set_persona_enabled(True)
    assert Settings(path).is_persona_enabled() is True


# --------------------------------------------------------------------------- #
# 陪伴模式（仅对话）
# --------------------------------------------------------------------------- #


def test_companion_chat_builds_messages():
    client = _CaptureClient()
    history = [
        {"role": "user", "content": "今天有点累"},
        {"role": "assistant", "content": "辛苦啦"},
        {"role": "system", "content": "应被丢弃"},
        {"role": "user", "content": "   "},  # 空内容丢弃
    ]

    reply = companion_chat(client, "陪我聊聊", history, model="m")

    assert reply == "已生成"
    messages = client.calls[0]
    # system：人格块 + 陪伴边界
    assert messages[0]["role"] == "system"
    assert "陪伴模式" in messages[0]["content"]
    assert "爱弥斯" in messages[0]["content"]
    # 闲聊温度更暖
    assert client.last_temperature == 0.8
    # 合法历史按序保留，当前消息收尾
    assert messages[1:] == [
        {"role": "user", "content": "今天有点累"},
        {"role": "assistant", "content": "辛苦啦"},
        {"role": "user", "content": "陪我聊聊"},
    ]


def test_companion_chat_history_limit():
    client = _CaptureClient()
    history = [{"role": "user", "content": f"第{i}句"} for i in range(25)]

    companion_chat(client, "最新", history, model="m")

    # system 之外只带最近 20 条历史 + 当前消息
    payload = client.calls[0][1:]
    assert len(payload) == 21
    assert payload[0]["content"] == "第5句"
    assert payload[-1]["content"] == "最新"


def test_chat_service_companion_short_circuit(tmp_path, monkeypatch):
    def fake_companion_chat(client, message, history, model, think_level):
        return "嗯，我在听"

    monkeypatch.setattr(
        "aemeath_agent.llm.agent_router.companion_chat", fake_companion_chat)
    settings = Settings(tmp_path / "settings.json")
    service = AgentChatService(manager=object(), settings=settings)

    out = asyncio.run(service.chat(
        "在吗", mode=MODE_COMPANION,
        history=[{"role": "user", "content": "在吗"}],
    ))

    assert out["reply"] == "嗯，我在听"
    assert out["agent_used"] is None
    assert out["result"] is None
    assert out["sandbox"] is None
    assert "陪伴" in out["thinking"][0]


# --------------------------------------------------------------------------- #
# 12 智能体改名（检测豹/安装豹/…/读豹）
# --------------------------------------------------------------------------- #


def test_agent_display_names():
    configs = DEFAULT_AGENT_CONFIGS
    assert len(configs) == 12
    expected = [
        "检测豹", "安装豹", "管家豹", "启动豹", "串门豹", "搭话豹",
        "看管豹", "音乐豹", "网络豹", "编辑豹", "搜索豹", "读豹",
    ]
    assert [c["display_name"] for c in configs] == expected
    assert all(c["display_name"].endswith("豹") for c in configs)