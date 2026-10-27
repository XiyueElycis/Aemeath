# -*- coding: utf-8 -*-
"""报错日志证据闭环测试。

覆盖：
- analyzer/error_paths：路径提取（命中/误报/去重）、根目录锚定与越界拒绝、
  只读核查（存在/缺失/0 字节/目录/越界）；
- LogAnalyzerAgent：analyze 端到端把报错引用路径核查为结构化证据、
  按签名查本地知识库、越界路径不访问、知识库异常不阻断；
- Orchestrator：上游任务结论摘要注入后续任务 data["upstream"]。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from aemeath_agent.agents.base_agent import AgentResult, AgentTask, BaseAgent
from aemeath_agent.agents.logs.log_analyzer_agent import (
    LogAnalyzerAgent,
    LogAnalysisResult,
    LogEntry,
)
from aemeath_agent.analyzer.error_paths import (
    check_referenced_paths,
    extract_referenced_paths,
    resolve_within_root,
)
from aemeath_agent.models import GameContext, KnowledgeHit
from aemeath_agent.orchestrator.orchestrator import (
    GameAgentOrchestrator,
    OrchestrationPlan,
    OrchestrationStage,
    _task,
)
from aemeath_agent.runtime import get_tracker


# --------------------------------------------------------------------------- #
# 路径提取
# --------------------------------------------------------------------------- #
def test_extract_relative_and_absolute_paths() -> None:
    text = (
        r"[ERROR] Unable to load mods/broken/foo.jar; "
        r"D:\Steam\common\GTAV\d3dx9_43.dll failed"
    )
    refs = extract_referenced_paths(text)
    assert "mods/broken/foo.jar" in refs
    assert r"D:\Steam\common\GTAV\d3dx9_43.dll" in refs


def test_extract_quoted_path_and_exclude_url() -> None:
    text = "missing texture `textures/player/a.png`, see https://x.com/a/x.jar"
    refs = extract_referenced_paths(text)
    assert refs == ["textures/player/a.png"]


def test_extract_rejects_false_positives() -> None:
    # Java 堆栈（无路径分隔符）、时间、版本号、裸文件名都不应被提取
    text = (
        "at com.foo.Bar(Bar.java:12) level 1.0.2 at 12:34 "
        "missing bare d3dx9_43.dll without directory"
    )
    assert extract_referenced_paths(text) == []


def test_extract_strips_trailing_punct_and_dedups() -> None:
    text = r"fail mods/a.jar, mods/a.jar; and mods/b.jar."
    refs = extract_referenced_paths(text)
    assert refs == ["mods/a.jar", "mods/b.jar"]


def test_extract_program_files_absolute() -> None:
    refs = extract_referenced_paths("x C:/Program Files/Game/data/core.pak missing")
    assert refs == ["C:/Program Files/Game/data/core.pak"]


# --------------------------------------------------------------------------- #
# 锚定与核查
# --------------------------------------------------------------------------- #
def test_resolve_relative_within_root(tmp_path: Path) -> None:
    target = resolve_within_root("mods/a.jar", tmp_path)
    assert target == (tmp_path / "mods" / "a.jar").resolve()


def test_resolve_rejects_out_of_root_absolute(tmp_path: Path) -> None:
    outside = r"C:\Windows\System32\ghost.dll"
    if os.name != "nt":
        pytest.skip("盘符越界用例仅 Windows")
    assert resolve_within_root(outside, tmp_path) is None


def test_check_paths_statuses(tmp_path: Path) -> None:
    (tmp_path / "mods" / "ok").mkdir(parents=True)
    (tmp_path / "mods" / "ok" / "real.jar").write_bytes(b"PK\x03\x04data")
    (tmp_path / "mods" / "ok" / "empty.dat").write_bytes(b"")

    text = (
        "error: mods/missing/bad.jar gone; "
        "mods/ok/real.jar present; "
        "mods/ok/empty.dat zero; "
        "mods/ok/ is a dir; "
        r"C:\Windows\System32\ghost.dll outside"
    )
    evidence = {e.raw: e for e in check_referenced_paths(text, tmp_path)}

    assert evidence["mods/missing/bad.jar"].status == "missing"
    real = evidence["mods/ok/real.jar"]
    assert real.status == "exists_file" and real.zero_byte is False and real.size == 8
    empty = evidence["mods/ok/empty.dat"]
    assert empty.status == "exists_file" and empty.zero_byte is True
    assert evidence["mods/ok/"].status == "exists_dir"
    out = evidence[r"C:\Windows\System32\ghost.dll"]
    assert out.status == "out_of_scope"
    assert "游戏目录之外" in out.reason


# --------------------------------------------------------------------------- #
# LogAnalyzerAgent 端到端
# --------------------------------------------------------------------------- #
class _FakeSQLiteRetriever:
    """替代真实知识库：仅对 UnsatisfiedLinkError 签名返回一条命中。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.queries: List[str] = []

    def search(self, signature: str) -> List[KnowledgeHit]:
        self.queries.append(signature)
        if "UnsatisfiedLinkError" in signature:
            return [KnowledgeHit(
                key="sig:UnsatisfiedLinkError",
                score=1.0,
                repair_template="安装/修复 VC++ 运行库后重启游戏验证",
            )]
        return []


def _make_game_with_log(root: Path) -> Path:
    """构造带缺失/0 字节/越界三类证据引用的游戏目录与日志。"""
    (root / "mods" / "ok").mkdir(parents=True)
    (root / "mods" / "ok" / "real.jar").write_bytes(b"PK\x03\x04ok")
    (root / "mods" / "ok" / "empty.dat").write_bytes(b"")
    log = root / "game.log"
    log.write_text(
        "[ERROR] Failed to load mods/missing/bad.jar (not found)\n"
        "java.lang.UnsatisfiedLinkError: native hook load failed\n"
        "error reading mods/ok/empty.dat\n"
        "fatal: C:\\Windows\\System32\\ghost.dll access denied\n"
        "info: mods/ok/real.jar loaded\n",
        encoding="utf-8",
    )
    ts = time.time()
    os.utime(log, (ts, ts))
    return log


def _analyze_agent(root: Path, monkeypatch: pytest.MonkeyPatch) -> AgentResult:
    import aemeath_agent.knowledge.retriever as retriever_mod

    fake = _FakeSQLiteRetriever()
    monkeypatch.setattr(retriever_mod, "SQLiteRetriever", lambda *a, **k: fake)

    agent = LogAnalyzerAgent()
    agent.initialize()

    async def _fake_llm(*args: Any, **kwargs: Any) -> tuple[str, List[str]]:
        return "模拟结论", ["模拟建议"]

    agent._llm_summarize = _fake_llm  # type: ignore[method-assign]

    task = AgentTask(name="log_analyzer", data={
        "operation": "analyze", "game_dir": str(root), "log_days": 0,
    })
    return asyncio.run(agent.execute(task))


def test_analyze_collects_evidence_and_knowledge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_game_with_log(tmp_path)
    result = _analyze_agent(tmp_path, monkeypatch)

    assert result.success
    data: LogAnalysisResult = result.data
    by_status: Dict[str, List[str]] = {}
    for ref in data.referenced_paths:
        by_status.setdefault(ref["status"], []).append(ref["raw"])

    assert "mods/missing/bad.jar" in by_status["missing"]
    assert "mods/ok/empty.dat" in [
        ref["raw"] for ref in data.referenced_paths if ref["zero_byte"]
    ]
    assert any(p == r"C:\Windows\System32\ghost.dll"
               for p in by_status.get("out_of_scope", []))
    assert data.knowledge_hits and data.knowledge_hits[0]["key"] == "sig:UnsatisfiedLinkError"
    assert "缺失" in result.message
    assert any("游戏目录外路径" in note for note in data.notes)
    assert data.summary == "模拟结论"  # LLM 路径仍生效，证据在其入参中


def test_analyze_reports_directory_access_without_touching_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_game_with_log(tmp_path)
    _analyze_agent(tmp_path, monkeypatch)

    messages = [
        f"{e.get('kind', '')}|{e.get('message', '')}|{e.get('path', '')}"
        for e in get_tracker().snapshot().get("events", [])
    ]
    joined = "\n".join(messages)
    # 目录级节流事件：按引用路径的父目录聚合
    assert "核查报错提及位置：mods/missing" in joined
    # 越界路径不得产生访问事件
    assert "System32" not in joined and "ghost.dll" not in joined


def test_signatures_derived_from_error_line() -> None:
    sigs = LogAnalyzerAgent._signatures_from_line(
        "Caused by: java.lang.UnsatisfiedLinkError: hook x64 failed"
    )
    # 异常类型名优先（短行原文可作为第二候选）
    assert sigs[0] == "UnsatisfiedLinkError"
    assert len(sigs) <= 2
    # 短中文行保留原文，覆盖中文签名库
    assert LogAnalyzerAgent._signatures_from_line("缺失 d3dx9_43.dll") == ["缺失 d3dx9_43.dll"]


def test_knowledge_lookup_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import aemeath_agent.knowledge.retriever as retriever_mod

    class _Boom:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("db down")

    monkeypatch.setattr(retriever_mod, "SQLiteRetriever", _Boom)
    entries = [LogEntry(file="x.log", line=1, text="error: boom")]
    assert LogAnalyzerAgent._knowledge_lookup(entries) == []


# --------------------------------------------------------------------------- #
# Orchestrator upstream 注入
# --------------------------------------------------------------------------- #
class _UpstreamCaptureAgent(BaseAgent):
    def __init__(self, name: str, seen: Dict[str, Any]):
        super().__init__(name)
        self.seen = seen

    def _do_initialize(self) -> None:
        pass

    async def execute(self, task: AgentTask) -> AgentResult:
        self.seen[task.name] = (task.data or {}).get("upstream")
        if task.name == "a":
            return AgentResult(
                success=True, message="a ok",
                data=LogAnalysisResult(
                    referenced_paths=[{"raw": "mods/missing.jar", "status": "missing"}],
                    knowledge_hits=[{"key": "sig:x", "repair_template": "tpl"}],
                ),
            )
        return AgentResult(success=True, message="b ok")


def test_upstream_brief_extracts_evidence() -> None:
    result = AgentResult(
        success=True,
        data=LogAnalysisResult(
            referenced_paths=[
                {"raw": "m.jar", "status": "missing"},
                {"raw": "z.jar", "status": "exists_file", "zero_byte": True},
            ],
            knowledge_hits=[{"key": "sig:1", "repair_template": ""}],
        ),
        message="x" * 500,
    )
    brief = GameAgentOrchestrator._upstream_brief({"log_analyzer": result})
    item = brief["log_analyzer"]
    assert item["missing_paths"] == ["m.jar"]
    assert item["zero_byte_paths"] == ["z.jar"]
    assert item["knowledge_hit_keys"] == ["sig:1"]
    assert len(item["message"]) <= 300


def test_sequential_stage_injects_upstream() -> None:
    seen: Dict[str, Any] = {}
    orch = GameAgentOrchestrator()
    orch.register_agents([
        _UpstreamCaptureAgent("a", seen),
        _UpstreamCaptureAgent("b", seen),
    ])
    plan = OrchestrationPlan(
        name="probe_upstream",
        description="upstream 注入验证",
        stages=[
            OrchestrationStage(
                name="s",
                tasks=[
                    _task("a", 10, {}),
                    _task("b", 20, {}, requires=["a"]),
                ],
                parallel=False,
            ),
        ],
    )
    out = asyncio.run(orch.execute_workflow(plan, GameContext(game_name="G")))

    assert out["success"] is True
    assert seen["a"] is None  # 首任务没有上游
    upstream_b = seen["b"]["a"]
    assert upstream_b["success"] is True
    assert upstream_b["missing_paths"] == ["mods/missing.jar"]
