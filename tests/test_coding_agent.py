"""编程智能体单元测试（工作区 / 工具 / ReAct 循环，全部离线）。"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys

import pytest

from aemeath_agent.coding import (
    CodingAgent,
    Workspace,
    WorkspaceError,
    build_repo_map,
    execute_tool,
    run_task,
)
from aemeath_agent.coding.tools import ApprovalAsk

# --------------------------------------------------------------------------- #
# 测试脚手架
# --------------------------------------------------------------------------- #


@pytest.fixture
def ws(tmp_path):
    """一个含少量源码的工作区。"""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "def add(a, b):\n    return a + b\n\n\n"
        "def sub(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("示例项目\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return Workspace(tmp_path)


class ScriptedLLM:
    """按队列返回文本，并记录每次收到的消息。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def complete_messages(self, messages, model=None, temperature=None,
                          max_tokens=None):
        self.calls.append(list(messages))
        return self.scripts.pop(0) if self.scripts else _final("没脚本了")


def _final(text="完成"):
    return json.dumps({"thought": "收尾", "final": text}, ensure_ascii=False)


def _act(tool, **args):
    return json.dumps({"thought": f"用 {tool}", "action": {"tool": tool,
                                                           "args": args}},
                      ensure_ascii=False)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 工作区：边界
# --------------------------------------------------------------------------- #
def test_workspace_rejects_path_escape(ws):
    for bad in ("../outside.txt", "pkg/../../etc", "/etc/passwd"):
        with pytest.raises(WorkspaceError):
            ws.resolve(bad)


def test_workspace_rejects_writes_into_protected_dirs(ws):
    with pytest.raises(WorkspaceError):
        ws.write_file(".git/config", "注入")
    with pytest.raises(WorkspaceError):
        ws.write_file("pkg/../.git/hooks/pre-commit", "x")


def test_workspace_lists_and_skips_blacklist(ws):
    entries = ws.list_dir(".", depth=2)
    assert "pkg/" in entries
    assert "pkg/core.py" in entries
    assert "README.md" in entries
    assert not any(".git" in e for e in entries)      # 版本库目录不展示


def test_workspace_read_with_line_range(ws):
    text = ws.read_file("pkg/core.py", start=4, end=5)
    assert "4|" in text and "def sub" in text
    assert "return a + b" not in text                 # 区间外不该出现


def test_workspace_read_rejects_directory_and_binary(ws, tmp_path):
    with pytest.raises(WorkspaceError):
        ws.read_file("pkg")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(WorkspaceError):
        ws.read_file("blob.bin")


def test_workspace_grep_and_glob(ws):
    hits = ws.grep(r"def \w+", glob_pattern="*.py")
    assert any("core.py:1:" in h for h in hits)
    assert all(".git" not in h for h in hits)
    assert ws.glob("*.py") == ["pkg/core.py"]
    assert ws.glob("*.md") == ["README.md"]


def test_workspace_edit_requires_unique_match(ws):
    assert ws.edit_file("pkg/core.py", "return a + b", "return b + a") \
        == "pkg/core.py"
    with pytest.raises(WorkspaceError):                 # 找不到
        ws.edit_file("pkg/core.py", "不存在的片段", "x")
    ws.write_file("dup.txt", "same\nsame\n")
    with pytest.raises(WorkspaceError):                 # 多处命中
        ws.edit_file("dup.txt", "same", "other")


# --------------------------------------------------------------------------- #
# 工具执行
# --------------------------------------------------------------------------- #
def test_readonly_tools_do_not_ask_for_approval(ws):
    asked = []

    def approve(ask):
        asked.append(ask)
        return True

    out = execute_tool("read_file", {"path": "pkg/core.py"}, ws, approve)
    assert out.ok and "def add" in out.content
    assert asked == []                                  # 只读不需要审批
    assert out.approved is None


def test_write_asks_with_diff_and_writes(ws):
    asked = []

    def approve(ask):
        asked.append(ask)
        return True

    out = execute_tool("write_file",
                       {"path": "pkg/new.py", "content": "X = 1\n"}, ws, approve)
    assert out.ok and out.approved is True
    assert asked[0].kind == "write" and asked[0].target == "pkg/new.py"
    assert (ws.root / "pkg" / "new.py").read_text(encoding="utf-8") == "X = 1\n"


def test_write_denied_leaves_file_untouched(ws):
    out = execute_tool("write_file",
                       {"path": "pkg/core.py", "content": "被覆盖"},
                       ws, lambda ask: False)
    assert out.ok is False and out.approved is False
    assert "拒绝" in out.content
    assert "def add" in (ws.root / "pkg" / "core.py").read_text(encoding="utf-8")


def test_edit_shows_diff_in_approval(ws):
    seen = {}

    def approve(ask):
        seen["diff"] = ask.diff
        return True

    out = execute_tool("edit_file",
                       {"path": "pkg/core.py", "old_text": "return a + b",
                        "new_text": "return a + b + 0"}, ws, approve)
    assert out.ok and "-    return a + b" in seen["diff"] \
        and "+    return a + b + 0" in seen["diff"]


def test_edit_reports_non_unique_match_without_asking(ws):
    asked = []
    out = execute_tool("edit_file",
                       {"path": "pkg/core.py", "old_text": "def", "new_text": "x"},
                       ws, lambda ask: asked.append(ask) or True)
    assert out.ok is False and "出现 2 次" in out.content
    assert asked == []                                   # 没命中就不该打扰用户


def test_unknown_tool_and_bad_args(ws):
    assert execute_tool("nope", {}, ws).ok is False
    assert execute_tool("read_file", {}, ws).ok is False           # 缺 path


def test_run_command_executes_in_root(ws):
    # 用当前解释器而不是裸 'python'：PATH 里未必有 python
    out = execute_tool(
        "run_command",
        {"command": f'"{sys.executable}" -c "print(open(\'README.md\','
                    ' encoding=\'utf-8\').read().strip())"'},
        ws, lambda ask: True)
    assert out.ok is True
    assert "示例项目" in out.content
    assert out.content.startswith("exit=0")


def test_run_command_sees_current_interpreter(ws):
    """命令环境里必须能直接叫到 python（本机 PATH 常没有）。"""
    out = execute_tool("run_command",
                       {"command": "python -c \"print(6 * 7)\""},
                       ws, lambda ask: True)
    assert out.ok is True and "42" in out.content


def test_run_command_denied_and_timeout(ws):
    denied = execute_tool("run_command", {"command": "echo hi"},
                          ws, lambda ask: False)
    assert denied.ok is False and "拒绝" in denied.content

    timed = execute_tool(
        "run_command",
        {"command": f'"{sys.executable}" -c "import time; time.sleep(5)"',
         "timeout": 1}, ws, lambda ask: True)
    assert timed.ok is False and "超时" in timed.content


# --------------------------------------------------------------------------- #
# ReAct 循环
# --------------------------------------------------------------------------- #
def test_agent_runs_tool_then_finishes(ws, tmp_path):
    llm = ScriptedLLM([
        _act("read_file", path="pkg/core.py"),
        _act("edit_file", path="pkg/core.py", old_text="return a - b",
             new_text="return a - b  # 已修正"),
        _final("已把 sub 的减法注释补上"),
    ])
    agent = CodingAgent(ws, client=llm, model="fake",
                        approve=lambda ask: True)

    result = run(agent.run("给 sub 的减法加一条注释"))

    assert result.finished is True
    assert result.error == ""
    assert [s.tool for s in result.steps] == ["read_file", "edit_file"]
    assert all(s.ok for s in result.steps)
    assert "# 已修正" in (tmp_path / "pkg" / "core.py").read_text(encoding="utf-8")
    # 观察结果确实回灌进了下一轮消息
    assert "【工具结果：read_file · 成功】" in llm.calls[1][-1]["content"]


def test_agent_stops_on_denied_write(ws, tmp_path):
    llm = ScriptedLLM([
        _act("write_file", path="pkg/core.py", content="全删了"),
        _final("用户拒绝了写入，我没有改动文件"),
    ])
    agent = CodingAgent(ws, client=llm, model="fake", approve=lambda ask: False)

    result = run(agent.run("重写 core.py"))

    assert result.steps[0].approved is False
    assert "拒绝了这次写入" in result.steps[0].observation
    assert result.finished is True
    assert "def add" in (tmp_path / "pkg" / "core.py").read_text(encoding="utf-8")


def test_agent_reports_progress_through_events(ws):
    events = []
    agent = CodingAgent(ws, client=ScriptedLLM([_final("没事可做")]),
                        model="fake",
                        on_event=lambda kind, payload: events.append(kind))
    run(agent.run("随便看看"))
    assert events[0] == "thought"
    assert "final" in events


def test_agent_aborts_on_repeated_calls(ws):
    """同一工具同一参数连刷 3 次 = 原地打转，必须停下来。"""
    llm = ScriptedLLM([_act("read_file", path="pkg/core.py")] * 6)
    agent = CodingAgent(ws, client=llm, model="fake")

    result = run(agent.run("读同一个文件"))

    assert result.finished is False
    assert "原地打转" in result.error
    assert len(result.steps) == 3


def test_agent_aborts_at_step_limit(ws):
    llm = ScriptedLLM([_act("glob_files", pattern=f"*.{i}") for i in range(10)])
    agent = CodingAgent(ws, client=llm, model="fake", max_steps=3)

    result = run(agent.run("一直找"))
    assert result.finished is False
    assert "步数上限" in result.error
    assert len(result.steps) == 3


def test_agent_retries_once_on_json_drift(ws):
    llm = ScriptedLLM(["我先看看文件", _final("直接回答你")])
    agent = CodingAgent(ws, client=llm, model="fake")

    result = run(agent.run("随便"))
    assert result.finished is True
    assert result.final == "直接回答你"


def test_agent_fails_loudly_when_drift_persists(ws):
    llm = ScriptedLLM(["散文一", "散文二"])
    agent = CodingAgent(ws, client=llm, model="fake")

    result = run(agent.run("随便"))
    assert result.finished is False
    assert "可解析的 JSON" in result.error


def test_agent_reports_client_failure(ws):
    class Boom:
        def complete_messages(self, *a, **k):
            raise RuntimeError("connection refused")

    result = run(CodingAgent(ws, client=Boom(), model="fake").run("随便"))
    assert result.finished is False
    assert "模型调用失败" in result.error


def test_empty_task_rejected(ws):
    result = run(CodingAgent(ws, client=ScriptedLLM([])).run("   "))
    assert result.finished is False
    assert "任务描述为空" in result.error


def test_run_task_helper(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    result = run(run_task("读一下 a.txt", root=str(tmp_path),
                          client=ScriptedLLM([
                              _act("read_file", path="a.txt"),
                              _final("a.txt 里是 hello")])))
    assert result.finished is True
    assert "hello" in result.steps[0].observation


def test_system_prompt_lists_every_tool(ws):
    llm = ScriptedLLM([_final()])
    run(CodingAgent(ws, client=llm, model="fake").run("看看"))
    system = llm.calls[0][0]["content"]
    for name in ("list_files", "read_file", "grep", "glob_files",
                 "write_file", "edit_file", "run_command"):
        assert name in system


def test_approval_ask_shape():
    ask = ApprovalAsk(kind="shell", target="ls", summary="列目录")
    assert ask.kind == "shell" and ask.diff == ""


# --------------------------------------------------------------------------- #
# 仓库地图
# --------------------------------------------------------------------------- #
def test_repo_map_lists_symbols_with_lines(ws):
    text = build_repo_map(ws)
    assert "pkg/core.py" in text
    assert "1: add" in text and "5: sub" in text
    assert "README.md" in text
    assert ".git" not in text                       # 版本库目录不进地图


def test_repo_map_scoped_and_bounded(ws):
    assert "README.md" not in build_repo_map(ws, "pkg")
    (ws.root / "big.py").write_text("def f():\n    pass\n", encoding="utf-8")
    out = build_repo_map(ws, max_files=1)
    assert out.count("  (") <= 1 or len(out.splitlines()) <= 5


def test_repo_map_tool_is_readonly(ws):
    asked = []
    out = execute_tool("repo_map", {}, ws, lambda a: asked.append(a) or True)
    assert out.ok and "pkg/core.py" in out.content
    assert asked == []


# --------------------------------------------------------------------------- #
# Git 工具
# --------------------------------------------------------------------------- #
_HAS_GIT = shutil.which("git") is not None
_needs_git = pytest.mark.skipif(not _HAS_GIT, reason="本机未安装 git")


def _init_repo(path):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=str(path),  # noqa: E731
                                   capture_output=True, text=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "tester")
    run("add", "-A")
    run("commit", "-q", "-m", "init")


def test_git_tools_report_non_repo(ws):
    """不是仓库（或没装 git）时给出可读提示，而不是抛异常。"""
    for tool in ("git_status", "git_diff", "git_log"):
        out = execute_tool(tool, {}, ws)
        assert out.ok is True                       # 提示不算失败
        assert "不是 Git 仓库" in out.content or "未检测到 git" in out.content


@_needs_git
def test_git_status_diff_and_log(ws):
    _init_repo(ws.root)
    (ws.root / "pkg" / "core.py").write_text(
        "def add(a, b):\n    return a + b  # 改了\n", encoding="utf-8")

    status_text = execute_tool("git_status", {}, ws).content
    assert "pkg/core.py" in status_text

    diff_text = execute_tool("git_diff", {}, ws).content
    assert "-def sub" in diff_text or "+    return a + b  # 改了" in diff_text

    log_text = execute_tool("git_log", {"limit": 3}, ws).content
    assert "init" in log_text


@_needs_git
def test_git_commit_requires_approval(ws):
    _init_repo(ws.root)
    target = ws.root / "pkg" / "core.py"
    target.write_text("def add(a, b):\n    return a + b\n# 改动\n",
                      encoding="utf-8")

    denied = execute_tool("git_commit", {"message": "改一下"},
                          ws, lambda ask: False)
    assert denied.ok is False and "拒绝" in denied.content
    assert "pkg/core.py" in execute_tool("git_status", {}, ws).content

    seen = {}

    def approve(ask):
        seen["summary"] = ask.summary
        seen["diff"] = ask.diff
        return True

    ok = execute_tool("git_commit", {"message": "改一下"}, ws, approve)
    assert ok.ok is True and "已提交" in ok.content
    assert "改一下" in seen["summary"]
    assert "pkg/core.py" in seen["diff"]          # 审批卡里带上了 status
    assert "干净" in execute_tool("git_status", {}, ws).content


@_needs_git
def test_git_commit_without_changes(ws):
    _init_repo(ws.root)
    out = execute_tool("git_commit", {"message": "空提交"}, ws, lambda a: True)
    assert out.ok is True and "没有需要提交" in out.content


@_needs_git
def test_git_commit_rejects_empty_message(ws):
    _init_repo(ws.root)
    out = execute_tool("git_commit", {"message": "  "}, ws, lambda a: True)
    assert out.ok is False and "message" in out.content


# --------------------------------------------------------------------------- #
# HTTP 接口
# --------------------------------------------------------------------------- #
def test_coding_tools_endpoint_lists_approval_flags(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aemeath_agent import server

    client = TestClient(server.app)
    resp = client.get("/coding/tools")
    assert resp.status_code == 200
    items = {item["name"]: item for item in resp.json()}
    assert items["read_file"]["approval"] is False
    assert items["write_file"]["approval"] is True
    assert items["git_commit"]["approval"] is True
    assert items["repo_map"]["approval"] is False


def test_coding_run_endpoint_plan_mode_is_readonly(tmp_path, monkeypatch):
    """plan 模式：读得到东西，但写入被拒、文件不变。"""
    from fastapi.testclient import TestClient

    from aemeath_agent import coding as coding_pkg
    from aemeath_agent import server

    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    original = coding_pkg.CodingAgent

    class _Patched(original):        # 注入脚本化模型，避免真调 LLM
        def _resolve_client(self):
            return ScriptedLLM([
                _act("write_file", path="a.py", content="X = 2\n"),
                _final("用户没批准，我没改"),
            ]), "fake"

    # server 里是 `from .coding import CodingAgent`，所以要打在包属性上
    monkeypatch.setattr(coding_pkg, "CodingAgent", _Patched)
    client = TestClient(server.app)
    resp = client.post("/coding/run", json={
        "task": "把 X 改成 2", "root": str(tmp_path), "mode": "plan"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "plan"
    assert data["steps"][0]["approved"] is False
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "X = 1\n"


def test_coding_run_endpoint_auto_writes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aemeath_agent import coding as coding_pkg
    from aemeath_agent import server

    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    original = coding_pkg.CodingAgent

    class _Patched(original):
        def _resolve_client(self):
            return ScriptedLLM([
                _act("write_file", path="a.py", content="X = 2\n"),
                _final("已改成 2"),
            ]), "fake"

    monkeypatch.setattr(coding_pkg, "CodingAgent", _Patched)
    client = TestClient(server.app)
    resp = client.post("/coding/run", json={
        "task": "把 X 改成 2", "root": str(tmp_path), "mode": "auto"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["finished"] is True and data["final"] == "已改成 2"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "X = 2\n"


def test_coding_run_endpoint_rejects_bad_input(tmp_path):
    from fastapi.testclient import TestClient

    from aemeath_agent import server

    client = TestClient(server.app)
    assert client.post("/coding/run", json={"task": "  "}).status_code == 400
    resp = client.post("/coding/run", json={
        "task": "x", "root": str(tmp_path / "不存在")})
    assert resp.status_code == 400
