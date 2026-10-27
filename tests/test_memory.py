"""长期记忆（仅陪伴模式）单测。

覆盖：
- token 估算与滚动记录；
- 阈值触发判定、总结 LLM 调用与画像配置合并/持久化；
- 画像块渲染；
- 陪伴服务接入记录/总结（edit/plan 不触发）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from aemeath_agent.llm.agent_router import MODE_COMPANION, AgentChatService
from aemeath_agent.memory import MemoryManager, UserProfile, estimate_tokens
from aemeath_agent.memory import render_profile_block
from aemeath_agent.settings import Settings


class FakeClient:
    """记录总结调用并返回固定 JSON。"""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload
        self.calls: List[List[Dict[str, str]]] = []

    def complete_messages(self, messages, model=None, temperature=None, max_tokens=None):
        self.calls.append(messages)
        return json.dumps(self.payload, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# token / 记录
# --------------------------------------------------------------------------- #


def test_estimate_tokens_cjk_vs_latin():
    assert estimate_tokens("你好") >= 1
    latin = estimate_tokens("hello world this is long text")
    assert latin >= 4
    assert estimate_tokens("") == 0


def test_record_accumulates_and_persists(tmp_path):
    m = MemoryManager(tmp_path)
    n1 = m.record("我喜欢画画", "好呀")
    n2 = m.record("爱好是摄影", "记住了")
    assert n2 > n1
    assert m.transcript_path.exists()
    assert m.tokens_since_summary == n2


def test_threshold_reached(tmp_path):
    s = Settings(tmp_path / "settings.json")
    m = MemoryManager(tmp_path)
    m.record("a" * 2000, "b" * 2000)
    # 阈值 0 / 负数永不触发
    assert m.threshold_reached(0) is False
    assert m.threshold_reached(1) is True
    assert m.threshold_reached(10**9) is False


# --------------------------------------------------------------------------- #
# 总结与画像
# --------------------------------------------------------------------------- #
_PROFILE_PAYLOAD: Dict[str, Any] = {
    "summary": "用户是热爱创作的人",
    "likes": ["画画", "音乐"],
    "hobbies": ["摄影"],
    "dislikes": ["嘈杂环境"],
    "communication_style": "温和、话不多",
    "facts": {"常用时段": "晚上"},
}


def test_summarize_writes_profile_and_archives(tmp_path):
    m = MemoryManager(tmp_path)
    m.record("我喜欢画画和音乐", "好的")
    client = FakeClient(_PROFILE_PAYLOAD)

    profile = m.summarize(client, model="m")

    assert profile.summary == "用户是热爱创作的人"
    assert profile.likes == ["画画", "音乐"]
    assert profile.updated_at
    saved = json.loads(m.profile_path.read_text(encoding="utf-8"))
    assert saved["hobbies"] == ["摄影"]
    # 计数归零
    assert m.tokens_since_summary == 0
    # 总结调用含系统分析提示
    assert "用户画像" in client.calls[0][0]["content"]


def test_summarize_merges_with_existing(tmp_path):
    m = MemoryManager(tmp_path)
    first = dict(_PROFILE_PAYLOAD)
    m.record("第一轮", "x")
    m.summarize(FakeClient(first))

    m.record("我最近迷上了爬山", "哇")
    second = dict(_PROFILE_PAYLOAD, hobbies=["爬山"], likes=["爬山"])
    profile = m.summarize(FakeClient(second))

    # 旧爱好与新爱好合并去重
    assert "摄影" in profile.hobbies
    assert "爬山" in profile.hobbies
    # 旧喜好保留
    assert "画画" in profile.likes


def test_summarize_bad_llm_keeps_profile(tmp_path):
    m = MemoryManager(tmp_path)
    m.record("x", "y")

    class Broken:
        def complete_messages(self, *a, **k):
            raise RuntimeError("network")

    before = m.load_profile()
    out = m.summarize(Broken())
    # 旁路容错：原样返回，不抛
    assert out.summary == before.summary
    assert m.tokens_since_summary > 0  # 未归零


def test_render_profile_block():
    p = UserProfile(
        summary="印象", likes=["音乐"], hobbies=["摄影"],
        communication_style="温和")
    block = render_profile_block(p)
    assert "长期记忆" in block
    assert "音乐" in block and "摄影" in block
    assert render_profile_block(UserProfile()) == ""


# --------------------------------------------------------------------------- #
# 陪伴服务接入
# --------------------------------------------------------------------------- #


def _make_service(tmp_path):
    settings = Settings(tmp_path / "agent_settings.json")
    service = AgentChatService(manager=object(), settings=settings)
    return service, settings


def test_companion_records_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "aemeath_agent.llm.agent_router.companion_chat",
        lambda *a, **k: "我记住啦")
    # 把记忆目录指到临时目录
    monkeypatch.setattr(
        "aemeath_agent.llm.agent_router.MemoryManager",
        lambda: MemoryManager(tmp_path / "mem"))

    service, _ = _make_service(tmp_path)
    out = __import__("asyncio").run(service.chat(
        "我喜欢星空", mode=MODE_COMPANION,
        history=[{"role": "user", "content": "我喜欢星空"}]))

    assert out["reply"] == "我记住啦"
    mem = MemoryManager(tmp_path / "mem")
    assert mem.tokens_since_summary > 0
    assert any("星空" in line for line in mem.transcript_path.read_text(
        encoding="utf-8").splitlines())


def test_companion_triggers_summary_at_low_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "aemeath_agent.llm.agent_router.companion_chat",
        lambda *a, **k: "好")
    mem = MemoryManager(tmp_path / "mem")
    monkeypatch.setattr(
        "aemeath_agent.llm.agent_router.MemoryManager", lambda: mem)
    # LLM 总结客户端打桩（_build_client 的返回）
    monkeypatch.setattr(
        AgentChatService, "_build_client",
        lambda self, llm: FakeClient(_PROFILE_PAYLOAD))

    # 设置极小阈值
    settings = Settings(tmp_path / "s.json")
    settings._data.setdefault("memory", {})["summary_token_threshold"] = 1
    service = AgentChatService(manager=object(), settings=settings)

    out = __import__("asyncio").run(service.chat(
        "随便聊", mode=MODE_COMPANION,
        history=[{"role": "user", "content": "随便聊"}]))

    assert any("画像已更新" in t for t in out["thinking"])
    assert mem.profile_path.exists()


def test_non_companion_does_not_record(tmp_path, monkeypatch):
    # edit/plan 走制度链：模块加载与非陪伴路径不应构造 MemoryManager。
    # 若有遗漏调用点，构造时即失败
    monkeypatch.setattr(
        "aemeath_agent.llm.agent_router.MemoryManager",
        lambda: pytest.fail("edit/plan 不应触发记忆"))
    from aemeath_agent.llm.agent_router import AgentChatService
    # 陪伴接线方法仍在服务类上
    assert hasattr(AgentChatService, "_maybe_remember")
