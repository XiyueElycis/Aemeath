"""编程智能体扩展能力测试：文件组织 / 检查点 / 权限 / 会话 / 压缩 / 流式 / 索引 / MCP。"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys

import pytest

from aemeath_agent.coding import (
    CheckpointStore,
    CodingAgent,
    PermissionStore,
    SessionStore,
    StdioMCPClient,
    Workspace,
    build_repo_map,
    execute_tool,
    search_code,
)
from aemeath_agent.coding.agent import KEEP_RECENT_STEPS, MAX_CONTEXT_CHARS
from aemeath_agent.coding.checkpoints import MAX_BATCHES
from aemeath_agent.coding.permissions import command_prefix

_HAS_GIT = shutil.which("git") is not None
_needs_git = pytest.mark.skipif(not _HAS_GIT, reason="本机未安装 git")

# --------------------------------------------------------------------------- #
# 脚手架
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    """隔离应用数据目录：检查点/权限/会话都不落真实用户目录。"""
    home = tmp_path / "_data"
    monkeypatch.setenv("AEMEATH_AGENT_DATA_HOME", str(home))
    return home


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "def login(user):\n    return user\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("示例\n", encoding="utf-8")
    return Workspace(tmp_path)


class ScriptedLLM:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def complete_messages(self, messages, model=None, temperature=None,
                          max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        return self.scripts.pop(0) if self.scripts else json.dumps(
            {"thought": "完了", "final": "done"}, ensure_ascii=False)


def _act(tool, **args):
    return json.dumps({"thought": "用工具", "action": {"tool": tool,
                                                      "args": args}},
                      ensure_ascii=False)


def _final(text="完成"):
    return json.dumps({"thought": "收尾", "final": text}, ensure_ascii=False)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 文件组织
# --------------------------------------------------------------------------- #
def test_make_dir_and_copy_and_move(ws, tmp_path):
    out = execute_tool("make_dir", {"path": "src/deep"}, ws, lambda a: True)
    assert out.ok and (tmp_path / "src" / "deep").is_dir()

    out = execute_tool("copy_path", {"src": "pkg/core.py",
                                     "dst": "src/deep/core.py"}, ws,
                       lambda a: True)
    assert out.ok
    assert (tmp_path / "src" / "deep" / "core.py").read_text(encoding="utf-8") \
        .startswith("def login")

    # 目标目录存在时表示移入其中
    out = execute_tool("move_path", {"src": "README.md", "dst": "src"}, ws,
                       lambda a: True)
    assert out.ok
    assert not (tmp_path / "README.md").exists()
    assert (tmp_path / "src" / "README.md").exists()


def test_move_and_copy_validation(ws):
    assert execute_tool("move_path", {"src": "不存在.py", "dst": "x"}, ws,
                        lambda a: True).ok is False
    assert execute_tool("move_path", {"src": "pkg/core.py", "dst": ""}, ws,
                        lambda a: True).ok is False
    # 同名同路径
    assert execute_tool("copy_path", {"src": "pkg/core.py",
                                      "dst": "pkg/core.py"}, ws,
                        lambda a: True).ok is False


def test_delete_file_and_directory_guard(ws, tmp_path):
    (tmp_path / "pkg" / "old.py").write_text("x = 1\n", encoding="utf-8")
    out = execute_tool("delete_path", {"path": "pkg/old.py"}, ws,
                       lambda a: True)
    assert out.ok and not (tmp_path / "pkg" / "old.py").exists()

    # 目录必须显式 recursive，否则拒绝（防止误删整棵树）
    guard = execute_tool("delete_path", {"path": "pkg"}, ws, lambda a: True)
    assert guard.ok is False and "recursive" in guard.content
    assert (tmp_path / "pkg").exists()

    out = execute_tool("delete_path", {"path": "pkg", "recursive": True},
                       ws, lambda a: True)
    assert out.ok and not (tmp_path / "pkg").exists()


def test_delete_approval_mentions_file_count(ws, tmp_path):
    seen = {}
    (tmp_path / "many").mkdir()
    for i in range(3):
        (tmp_path / "many" / f"f{i}.py").write_text("x\n", encoding="utf-8")

    execute_tool("delete_path", {"path": "many", "recursive": True}, ws,
                 lambda ask: seen.update(summary=ask.summary) or True)
    assert "3 个文件" in seen["summary"]


def test_organization_tools_respect_protected_dirs(ws):
    for tool, args in [
        ("make_dir", {"path": ".git/hooks"}),
        ("delete_path", {"path": ".git", "recursive": True}),
    ]:
        out = execute_tool(tool, args, ws, lambda a: True)
        assert out.ok is False, tool


def test_denied_organization_leaves_fs_untouched(ws, tmp_path):
    out = execute_tool("make_dir", {"path": "newdir"}, ws, lambda a: False)
    assert out.ok is False and not (tmp_path / "newdir").exists()


# --------------------------------------------------------------------------- #
# 检查点 / 撤销
# --------------------------------------------------------------------------- #
def test_write_creates_checkpoint_and_undo_restores(ws, tmp_path):
    target = tmp_path / "pkg" / "core.py"
    original = target.read_text(encoding="utf-8")

    execute_tool("write_file", {"path": "pkg/core.py", "content": "坏掉了\n"},
                 ws, lambda a: True)
    assert target.read_text(encoding="utf-8") == "坏掉了\n"

    out = execute_tool("undo_change", {"path": "pkg/core.py"}, ws,
                       lambda a: True)
    assert out.ok and "还原 pkg/core.py" in out.content
    assert target.read_text(encoding="utf-8") == original


def test_undo_removes_newly_created_file(ws, tmp_path):
    execute_tool("write_file", {"path": "pkg/new.py", "content": "X = 1\n"},
                 ws, lambda a: True)
    assert (tmp_path / "pkg" / "new.py").exists()

    execute_tool("undo_change", {"path": "pkg/new.py"}, ws, lambda a: True)
    assert not (tmp_path / "pkg" / "new.py").exists()


def test_undo_restores_deleted_file(ws, tmp_path):
    target = tmp_path / "README.md"
    execute_tool("delete_path", {"path": "README.md"}, ws, lambda a: True)
    assert not target.exists()

    execute_tool("undo_change", {}, ws, lambda a: True)   # 还原最近一批
    assert target.exists()


def test_list_checkpoints_shows_batches(ws):
    execute_tool("write_file", {"path": "README.md", "content": "新\n"}, ws,
                 lambda a: True)
    out = execute_tool("list_checkpoints", {}, ws)
    assert out.ok and "个文件" in out.content


def test_undo_without_history_fails_cleanly(ws):
    out = execute_tool("undo_change", {}, ws, lambda a: True)
    assert out.ok is False and "快照" in out.content


def test_checkpoints_trimmed(tmp_path, data_home):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    store = CheckpointStore(root)
    for _ in range(MAX_BATCHES + 5):
        store.snapshot([root / "a.txt"])
    assert len(store.list()) == MAX_BATCHES


def test_checkpoints_live_outside_workspace(ws, tmp_path, data_home):
    """快照不能写进用户仓库。"""
    execute_tool("write_file", {"path": "README.md", "content": "改\n"}, ws,
                 lambda a: True)
    assert not any(p.name == "checkpoints" for p in tmp_path.iterdir())
    assert any(data_home.glob("coding/checkpoints/*/*/manifest.json"))


# --------------------------------------------------------------------------- #
# 权限记忆
# --------------------------------------------------------------------------- #
class _Ask:
    def __init__(self, kind, target, tool):
        self.kind, self.target, self.tool = kind, target, tool


def test_permission_remembers_tool(ws):
    store = PermissionStore(ws.root)
    ask = _Ask("write", "pkg/core.py", "write_file")
    assert store.is_allowed(ask) is False

    store.remember(ask)
    assert store.is_allowed(ask) is True
    # 存到磁盘、重建后仍生效
    assert PermissionStore(ws.root).is_allowed(ask) is True


def test_permission_command_prefix_scope(ws):
    store = PermissionStore(ws.root)
    store.remember(_Ask("shell", "python -m pytest -q", "run_command"))

    assert store.is_allowed(_Ask("shell", "python -m pytest tests", "run_command"))
    # 前缀不同就不放行——避免一条规则把整台机器放开
    assert store.is_allowed(_Ask("shell", "python -m pip install x", "run_command")) is False
    assert store.is_allowed(_Ask("shell", "rm -rf build", "run_command")) is False
    assert command_prefix("python -m pytest -q tests") == "python -m pytest"


def test_permission_reset(ws):
    store = PermissionStore(ws.root)
    ask = _Ask("write", "a.py", "write_file")
    store.remember(ask)
    store.reset()
    assert store.is_allowed(ask) is False


def test_agent_skips_prompt_when_allowed(ws):
    """命中长期规则时不再打扰用户，且实际写入生效。"""
    store = PermissionStore(ws.root)
    store.remember(_Ask("write", "README.md", "write_file"))
    asked = []

    agent = CodingAgent(
        ws, client=ScriptedLLM([
            _act("write_file", path="README.md", content="改写\n"),
            _final("已改")]),
        model="fake",
        approve=lambda ask: asked.append(ask) or False,   # 用户本该拒绝
        permissions=store)
    result = run(agent.run("改 README"))
    assert result.steps[0].approved is True
    assert asked == []                                   # 没问用户
    assert (ws.root / "README.md").read_text(encoding="utf-8") == "改写\n"


# --------------------------------------------------------------------------- #
# 会话持久化
# --------------------------------------------------------------------------- #
def test_session_saved_and_listed(ws, data_home):
    store = SessionStore()
    sid = store.save(task="改 bug", root=str(ws.root),
                     messages=[{"role": "user", "content": "x"}],
                     steps=[{"index": 1, "tool": "read_file"}],
                     finished=True, final="好了")
    rows = store.list()
    assert rows[0]["id"] == sid and rows[0]["task"] == "改 bug"
    assert rows[0]["steps"] == 1 and rows[0]["finished"] is True
    assert store.load(sid)["final"] == "好了"


def test_agent_writes_session_and_resumes(ws):
    store = SessionStore()
    agent = CodingAgent(ws, client=ScriptedLLM([
        _act("read_file", path="README.md"), _final("看过 README")]),
        model="fake", sessions=store)
    first = run(agent.run("先看看 README"))
    assert first.session_id and store.load(first.session_id)

    # 续做：历史被带回上下文，但不会带上旧的 system 说明
    llm2 = ScriptedLLM([_final("接着做完了")])
    agent2 = CodingAgent(ws, client=llm2, model="fake", sessions=store,
                         session_id=first.session_id)
    second = run(agent2.run("继续"))
    assert second.finished
    sent = llm2.calls[0]
    assert sum(1 for m in sent if m["role"] == "system") == 1
    # 上一轮的收尾结论确实被带回上下文（模型输出是 JSON 信封，按包含判断）
    assert any("看过 README" in m["content"] for m in sent)
    # 保存时复用了同一个会话 id
    assert second.session_id == first.session_id


def test_agent_without_session_store_does_not_crash(ws):
    result = run(CodingAgent(ws, client=ScriptedLLM([_final()]),
                             model="fake").run("随便"))
    assert result.finished and result.session_id == ""


# --------------------------------------------------------------------------- #
# 上下文压缩
# --------------------------------------------------------------------------- #
def test_compact_elides_middle_tool_turns(ws):
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "任务"}]
    for i in range(20):
        messages.append({"role": "assistant", "content": _act("read_file", path="a")})
        messages.append({"role": "user",
                         "content": f"【工具结果：read_file · 成功】\n" + "x" * 4000})
    assert sum(len(m["content"]) for m in messages) > MAX_CONTEXT_CHARS

    CodingAgent._compact(messages)

    assert messages[0]["content"] == "sys"
    assert messages[1]["content"] == "任务"
    assert any("已省略" in m["content"] for m in messages)
    # 最近几步原样保留
    assert messages[-1]["content"].endswith("x" * 100)
    assert len(messages) <= 2 + 1 + KEEP_RECENT_STEPS * 2 + 1


def test_compact_noop_under_budget():
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "任务"}]
    CodingAgent._compact(messages)
    assert len(messages) == 2


def test_agent_compacts_long_run(ws):
    """跑很多步后，送给模型的消息体积仍受控。"""
    # 每步换一个参数，否则会先被「原地打转」守卫拦下
    scripts = [_act("read_file", path=f"README.md", start=i + 1)
               for i in range(12)] + [_final()]
    llm = ScriptedLLM(scripts)
    agent = CodingAgent(ws, client=llm, model="fake", max_steps=13)

    import aemeath_agent.coding.agent as agent_mod
    from aemeath_agent.coding.tools import ToolOutcome

    original = agent_mod.execute_tool
    try:
        # 签名与 execute_tool 一致（agent 会传入 settings）
        agent_mod.execute_tool = (
            lambda name, args, work, ap=None, settings=None:
            ToolOutcome(True, "y" * 5000, "read_file"))
        result = run(agent.run("读很多次"))
    finally:
        agent_mod.execute_tool = original

    assert result.tool_calls == 12
    assert result.finished is True
    last = llm.calls[-1]
    assert sum(len(m["content"]) for m in last) < MAX_CONTEXT_CHARS + 10_000
    assert any("已省略" in m["content"] for m in last)


# --------------------------------------------------------------------------- #
# 代码索引
# --------------------------------------------------------------------------- #
def test_search_code_finds_relevant_chunk(ws):
    (ws.root / "auth.py").write_text(
        "def login(user, pwd):\n    \"\"\"处理登录校验\"\"\"\n    return True\n",
        encoding="utf-8")
    (ws.root / "mathx.py").write_text("def add(a, b):\n    return a + b\n",
                                      encoding="utf-8")
    hits = search_code(ws, "登录 校验", limit=2)
    assert hits and "auth.py" in hits[0]


def test_search_code_falls_back_to_grep(ws):
    """检索不到时退回字面匹配，别让模型以为仓库里真没有。"""
    hits = search_code(ws, "def login", limit=3)
    assert any("core.py" in h for h in hits)


def test_search_code_tool_is_readonly(ws):
    asked = []
    out = execute_tool("search_code", {"query": "login"}, ws,
                       lambda a: asked.append(a) or True)
    assert out.ok and "core.py" in out.content
    assert asked == []


# --------------------------------------------------------------------------- #
# MCP stdio 客户端
# --------------------------------------------------------------------------- #
_FAKE_SERVER = r'''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        out = {"jsonrpc": "2.0", "id": mid,
               "result": {"protocolVersion": "2024-11-05",
                          "serverInfo": {"name": "fake", "version": "0.1"}}}
    elif method == "tools/list":
        out = {"jsonrpc": "2.0", "id": mid,
               "result": {"tools": [{"name": "echo",
                                     "description": "回显",
                                     "inputSchema": {}}]}}
    elif method == "tools/call":
        text = (msg.get("params") or {}).get("arguments", {}).get("text", "")
        out = {"jsonrpc": "2.0", "id": mid,
               "result": {"content": [{"type": "text", "text": "echo:" + text}]}}
    else:
        out = {"jsonrpc": "2.0", "id": mid, "result": {}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
'''


def test_mcp_client_handshake_and_call(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")

    with StdioMCPClient([sys.executable, str(script)], timeout=10) as client:
        info = client.server_info
        assert info["name"] == "fake"
        tools = client.list_tools()
        assert tools[0]["name"] == "echo"
        assert client.call_tool("echo", {"text": "hi"}) == "echo:hi"


def test_mcp_client_reports_missing_command():
    from aemeath_agent.coding import MCPError

    client = StdioMCPClient(["definitely-not-a-real-command-xyz"], timeout=3)
    with pytest.raises(MCPError):
        client.start()


def test_mcp_client_timeout(tmp_path):
    from aemeath_agent.coding import MCPError

    script = tmp_path / "silent.py"
    script.write_text("import sys, time\ntime.sleep(30)\n", encoding="utf-8")
    client = StdioMCPClient([sys.executable, str(script)], timeout=1.0)
    try:
        with pytest.raises(MCPError):
            client.start()
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# HTTP：会话列表 / 流式
# --------------------------------------------------------------------------- #
def test_coding_sessions_endpoint(ws):
    from fastapi.testclient import TestClient

    from aemeath_agent import server

    SessionStore().save(task="演示", root=str(ws.root), messages=[],
                        steps=[], finished=True, final="嗯")
    resp = TestClient(server.app).get("/coding/sessions")
    assert resp.status_code == 200
    assert resp.json()[0]["task"] == "演示"


def test_coding_stream_endpoint_emits_events(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aemeath_agent import coding as coding_pkg
    from aemeath_agent import server

    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    original = coding_pkg.CodingAgent

    class _Patched(original):
        def _resolve_client(self):
            return ScriptedLLM([
                _act("read_file", path="a.py"), _final("看完了")]), "fake"

    monkeypatch.setattr(coding_pkg, "CodingAgent", _Patched)
    client = TestClient(server.app)
    with client.stream("POST", "/coding/run/stream", json={
            "task": "读 a.py", "root": str(tmp_path), "mode": "plan"}) as resp:
        assert resp.status_code == 200
        body = "".join(chunk for chunk in resp.iter_text())

    events = [json.loads(line[6:]) for line in body.splitlines()
              if line.startswith("data: ")]
    kinds = [e["event"] for e in events]
    assert "thought" in kinds and "action" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["finished"] is True


def test_repo_map_and_tools_list_include_new_tools():
    from aemeath_agent.coding.tools import TOOL_SPECS

    for name in ("make_dir", "move_path", "copy_path", "delete_path",
                 "undo_change", "list_checkpoints", "search_code"):
        assert name in TOOL_SPECS


# --------------------------------------------------------------------------- #
# JSON 解析健壮性（真机上遇到过的形状）
# --------------------------------------------------------------------------- #
def test_parse_json_survives_trailing_think_block():
    """推理模型在 JSON 后追加 </think> 与半截思考时仍能解出对象。"""
    from aemeath_agent.llm.agent_router import parse_json

    raw = ('{"thought": "移动文件", "action": {"tool": "move_path", '
           '"args": {"src": "a.py", "dst": "lib"}}}</think>\n'
           '<think>用户要求把 a.py 移进 lib，参数里 {dst} 是目录')
    data = parse_json(raw)
    assert data["action"]["tool"] == "move_path"


def test_parse_json_handles_fenced_and_prose():
    from aemeath_agent.llm.agent_router import parse_json

    assert parse_json('```json\n{"final": "好了"}\n```')["final"] == "好了"
    assert parse_json('先说两句 {"final": "嗯"} 再说两句')["final"] == "嗯"
    assert parse_json("完全没有 JSON") == {}


# --------------------------------------------------------------------------- #
# 多动作（并行只读 / 串行写入）
# --------------------------------------------------------------------------- #
def test_normalize_actions_supports_both_shapes():
    from aemeath_agent.coding.agent import _normalize_actions

    one = _normalize_actions({"action": {"tool": "read_file",
                                         "args": {"path": "a"}}})
    assert one == [("read_file", {"path": "a"})]

    many = _normalize_actions({"actions": [
        {"tool": "read_file", "args": {"path": "a"}},
        {"tool": "grep", "args": {"pattern": "x"}},
        {"tool": "", "args": {}},              # 缺工具名，忽略
        "不是对象",                              # 忽略
    ]})
    assert [n for n, _ in many] == ["read_file", "grep"]
    assert _normalize_actions({}) == []


def test_agent_runs_multiple_actions_in_one_step(ws, tmp_path):
    (tmp_path / "b.py").write_text("Y = 2\n", encoding="utf-8")
    multi = json.dumps({"thought": "并行读两个文件", "actions": [
        {"tool": "read_file", "args": {"path": "pkg/core.py"}},
        {"tool": "read_file", "args": {"path": "b.py"}},
    ]}, ensure_ascii=False)
    llm = ScriptedLLM([multi, _final("读完了")])

    result = run(CodingAgent(ws, client=llm, model="fake").run("读两个文件"))

    assert result.finished is True
    assert [s.tool for s in result.steps] == ["read_file", "read_file"]
    assert all(s.ok for s in result.steps)
    # 观察文本合并成一条 user 消息，按序号可区分
    last_user = llm.calls[-1][-1]["content"]
    assert "1/2" in last_user and "2/2" in last_user
    assert "def login" in result.steps[0].observation
    assert "Y = 2" in result.steps[1].observation


def test_agent_multi_actions_write_in_order(ws, tmp_path):
    multi = json.dumps({"thought": "建两个文件", "actions": [
        {"tool": "write_file", "args": {"path": "x.py", "content": "X = 1\n"}},
        {"tool": "write_file", "args": {"path": "y.py", "content": "Y = 1\n"}},
    ]}, ensure_ascii=False)
    agent = CodingAgent(ws, client=ScriptedLLM([multi, _final("建好了")]),
                        model="fake", approve=lambda ask: True)
    run(agent.run("建两个文件"))

    assert (tmp_path / "x.py").read_text(encoding="utf-8") == "X = 1\n"
    assert (tmp_path / "y.py").read_text(encoding="utf-8") == "Y = 1\n"


# --------------------------------------------------------------------------- #
# 分支 / 推送 / PR
# --------------------------------------------------------------------------- #
@_needs_git
def test_git_branch_and_checkout(ws):
    _init_repo(ws.root)
    out = execute_tool("git_branch", {}, ws)
    assert out.ok and ("main" in out.content or "master" in out.content)

    created = execute_tool("git_checkout", {"branch": "feature/x", "create": True},
                           ws, lambda a: True)
    assert created.ok and "feature/x" in created.content
    assert "*" in execute_tool("git_branch", {}, ws).content

    denied = execute_tool("git_checkout", {"branch": "master"}, ws,
                          lambda a: False)
    assert denied.ok is False


def test_git_pr_without_gh_explains(tmp_path, monkeypatch):
    """没有 gh 时给出明确指引，而不是报一个看不懂的错。"""
    from aemeath_agent.coding import git as git_mod

    monkeypatch.setattr(git_mod.shutil, "which", lambda name: None)
    out = execute_tool("git_pr", {"title": "标题"},
                       approve=lambda a: True, workspace=_FakeWs(tmp_path))
    assert out.ok is False and "gh" in out.content


class _FakeWs:
    """最小工作区桩（git_pr 只需 root 属性）。"""

    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)


@_needs_git
def test_git_push_requires_repo(ws):
    out = execute_tool("git_push", {}, ws, lambda a: False)
    assert out.ok is False


# --------------------------------------------------------------------------- #
# MCP 注册与调用
# --------------------------------------------------------------------------- #
def _settings(tmp_path):
    from aemeath_agent.settings import Settings

    return Settings(tmp_path / "agent_settings.json")


def test_settings_mcp_server_crud(tmp_path):
    s = _settings(tmp_path)
    assert s.get_mcp_servers() == []

    s.upsert_mcp_server("demo", ["python", "server.py"], timeout=12)
    rows = s.get_mcp_servers()
    assert rows[0]["name"] == "demo"
    assert rows[0]["command"] == ["python", "server.py"]
    assert rows[0]["timeout"] == 12

    # 同名更新而不是重复追加
    s.upsert_mcp_server("demo", "uvx demo", enabled=False)
    rows = s.get_mcp_servers()
    assert len(rows) == 1 and rows[0]["command"] == ["uvx demo"]
    assert rows[0]["enabled"] is False

    assert s.remove_mcp_server("demo") is True
    assert s.remove_mcp_server("demo") is False
    assert s.get_mcp_servers() == []


def test_settings_mcp_server_validation(tmp_path):
    s = _settings(tmp_path)
    with pytest.raises(ValueError):
        s.upsert_mcp_server("", ["x"])
    with pytest.raises(ValueError):
        s.upsert_mcp_server("x", [])


def test_mcp_servers_call_tool(tmp_path):
    import aemeath_agent.coding.mcp_servers as mcp_servers

    script = tmp_path / "server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    s = _settings(tmp_path)
    s.upsert_mcp_server("fake", [sys.executable, str(script)], timeout=10)

    try:
        listing = mcp_servers.list_tools(s)
        assert "fake" in listing and "echo" in listing
        assert mcp_servers.call_tool(s, "fake", "echo", {"text": "hi"}) == "echo:hi"
        # 第二次调用复用连接（不会重新拉起进程），仍能正常列出工具
        assert "echo" in mcp_servers.list_tools(s, "fake")
    finally:
        mcp_servers.close_all()


def test_mcp_servers_unknown_server(tmp_path):
    from aemeath_agent.coding import MCPError
    import aemeath_agent.coding.mcp_servers as mcp_servers

    s = _settings(tmp_path)
    with pytest.raises(MCPError):
        mcp_servers.call_tool(s, "不存在", "x")
    assert "还没有注册" in mcp_servers.list_tools(s)


def test_mcp_tools_require_approval(ws, tmp_path):
    """mcp_call 必须过审批；mcp_list 不需要。"""
    from aemeath_agent.coding.tools import READONLY_TOOLS, WRITE_TOOLS

    assert "mcp_list" in READONLY_TOOLS
    assert "mcp_call" in WRITE_TOOLS

    asked = []
    out = execute_tool("mcp_call", {"server": "x", "tool": "y"}, ws,
                       lambda a: asked.append(a) or False, _settings(tmp_path))
    assert out.ok is False and asked and asked[0].tool == "mcp_call"


def test_mcp_http_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aemeath_agent import server

    settings = _settings(tmp_path)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    client = TestClient(server.app)

    created = client.put("/mcps/servers", json={
        "name": "demo", "command": [sys.executable, "-c", "print(1)"]})
    assert created.status_code == 200
    assert created.json()["name"] == "demo"

    assert client.get("/mcps/servers").json()[0]["name"] == "demo"

    bad = client.put("/mcps/servers", json={"name": "", "command": []})
    assert bad.status_code == 400

    # probe 一个必然启动不了的命令：返回 ok=False 而不是 500
    settings.upsert_mcp_server("broken", ["definitely-not-real-cmd"], timeout=3)
    probe = client.post("/mcps/servers/broken/probe")
    assert probe.status_code == 200 and probe.json()["ok"] is False

    assert client.delete("/mcps/servers/demo").status_code == 200
    assert client.delete("/mcps/servers/demo").status_code == 404
    assert client.post("/mcps/servers/close").json()["closed"] is True
