"""落盘智能体沙箱接入测试（TR-3.1~3.4）。

覆盖 script_editor 四类写操作在沙箱/直接两种模式下的行为、web_search 下载
重定向、game_content（saves 子项）备份进 side 区；真实游戏目录在沙箱模式下零变化。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from gamedoctor.agents.base_agent import AgentTask
from gamedoctor.agents.content.game_content_agent import GameContentAgent
from gamedoctor.agents.scripts.script_editor_agent import ScriptEditorAgent
from gamedoctor.agents.web.web_search_agent import WebSearchAgent
from gamedoctor.sandbox import SandboxSession
from gamedoctor.sandbox import io as sxio


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    apphome = tmp_path / "apphome"
    fakehome = tmp_path / "home"
    monkeypatch.setattr("gamedoctor.sandbox.session.app_dir", lambda: apphome)
    monkeypatch.setattr(Path, "home", lambda: fakehome)

    game = tmp_path / "game"
    (game / "sub").mkdir(parents=True)
    (game / "config.ini").write_text("[main]\nkey=old\n", encoding="utf-8")
    (game / "target.txt").write_text("target-old", encoding="utf-8")
    (game / "source.txt").write_text("source-content", encoding="utf-8")

    saves = game / "saves"
    saves.mkdir()
    (saves / "slot1.sav").write_bytes(b"SAVE1")

    def make(ticket: str = "AG-1") -> SandboxSession:
        return SandboxSession(ticket, game, sandbox_base=apphome / "sandbox")

    return SimpleNamespace(game=game, saves=saves, apphome=apphome, make=make)


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _editor() -> ScriptEditorAgent:
    a = ScriptEditorAgent()
    a.initialize()
    return a


def _task(data: dict) -> AgentTask:
    return AgentTask(name="script_editor", agent="script_editor", data=data)


@pytest.fixture
def fake_llm_edit(monkeypatch: pytest.MonkeyPatch):
    """让 script_editor 的 LLM 生成步骤返回固定新内容。"""
    async def _fake(self, target, lang, original, instruction):  # noqa: ANN001
        return f"{instruction}-result"

    monkeypatch.setattr(ScriptEditorAgent, "_generate_content", _fake)


# --------------------------------------------------------------------- #
# TR-3.1 script_editor 沙箱写操作零真实落盘
# --------------------------------------------------------------------- #
def test_script_editor_all_writes_redirected(env, fake_llm_edit) -> None:
    before = snapshot(env.game)
    s = env.make()
    ed = _editor()

    # edit
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "config.ini",
        "operation": "edit", "user_message": "EDIT", "_sandbox_session": s})))
    assert r.success, r.message
    assert "沙箱" in r.message

    # create
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "new.lua",
        "operation": "create", "user_message": "CREATE", "_sandbox_session": s})))
    assert r.success, r.message

    # replace
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "target.txt",
        "operation": "replace", "source": "source.txt",
        "_sandbox_session": s})))
    assert r.success, r.message
    # replace 后 overlay 内容为源文件内容，且覆盖前备份进 side
    assert (s.overlay_dir / "target.txt").read_text(encoding="utf-8") == "source-content"
    assert list(s.side_dir.rglob("target.txt"))

    # delete（替换稿被删 → 净零：overlay 移除、tombstone 不复活真实文件）
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "target.txt",
        "operation": "delete", "_sandbox_session": s})))
    assert r.success, r.message

    # 真实目录零变化
    assert snapshot(env.game) == before
    # overlay 产物
    assert (s.overlay_dir / "config.ini").read_text(encoding="utf-8") == "EDIT-result"
    assert (s.overlay_dir / "new.lua").read_text(encoding="utf-8") == "CREATE-result"
    assert not (s.overlay_dir / "target.txt").exists()
    assert "target.txt" in s.changes and s.changes["target.txt"].op == "delete"
    # read 透传 + list 合并不报错
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "config.ini",
        "operation": "read", "_sandbox_session": s})))
    assert r.success and r.data.content == "EDIT-result"
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "operation": "list", "_sandbox_session": s})))
    assert r.success and any(x["name"] == "new.lua" for x in r.data.listing)


def test_script_editor_direct_mode_hits_real_disk(env, fake_llm_edit) -> None:
    ed = _editor()
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "config.ini",
        "operation": "edit", "user_message": "DIRECT"})))
    assert r.success, r.message
    assert (env.game / "config.ini").read_text(encoding="utf-8") == "DIRECT-result"
    # 未创建任何沙箱目录
    assert not (env.apphome / "sandbox").exists()


def test_script_editor_sandbox_cross_root_absolute_rejected(env) -> None:
    s = env.make()
    ed = _editor()
    outside = env.game.parent / "evil.ini"
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": str(outside),
        "operation": "create", "user_message": "X", "_sandbox_session": s})))
    assert r.success is False
    assert "授权根" in r.message or "沙箱" in r.message
    assert not outside.exists()


# --------------------------------------------------------------------- #
# TR-3.2 web_search 下载进 overlay
# --------------------------------------------------------------------- #
class _FakeResp:
    content = b"downloaded-bytes"

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        return _FakeResp()


def test_web_download_redirected(env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
    before = snapshot(env.game)
    s = env.make()
    agent = WebSearchAgent()
    agent.initialize()

    r = asyncio_run(agent.execute(_task({
        "game_dir": str(env.game), "url": "https://example.com/patch.bin",
        "file": "patch.bin", "_sandbox_session": s})))
    assert r.success, r.message
    assert snapshot(env.game) == before
    assert (s.overlay_dir / "patch.bin").read_bytes() == b"downloaded-bytes"
    assert s.changes["patch.bin"].op == "download"


# --------------------------------------------------------------------- #
# TR-3.3 game_content(saves) 备份进 side
# --------------------------------------------------------------------- #
def test_save_backup_goes_to_side(env) -> None:
    s = env.make()
    agent = GameContentAgent()
    agent.initialize()
    r = asyncio_run(agent.execute(AgentTask(
        name="save_management", agent="game_content",
        data={"game_name": "GameX", "op": "saves",
              "save_path": str(env.saves), "_sandbox_session": s})))
    assert r.success, r.message
    # 真实备份目录无写入
    assert not (Path.home() / ".gamedoctor" / "saves_backup" / "GameX").exists()
    # side 区内有存档副本
    assert list(s.side_dir.rglob("slot1.sav"))
    assert any(c.op == "side_write" for c in s.changes.values())


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# --------------------------------------------------------------------- #
# 目录不得走文件原语（Windows 原生只报 Permission denied，易误诊为权限问题）
# --------------------------------------------------------------------- #
def test_copy_file_on_directory_raises_clear_error(env) -> None:
    d = env.game / "sub"
    # 直接模式与沙箱模式都应给出明确的"是目录"错误，而非 PermissionError
    with pytest.raises(IsADirectoryError, match="目录"):
        sxio.copy_file(None, d, env.game.parent / "bak-direct" / "sub")
    s = env.make()
    with pytest.raises(IsADirectoryError, match="目录"):
        sxio.copy_file(s, d, env.game.parent / "bak-sb" / "sub", side_dst=True)


def test_remove_directory_raises_clear_error(env) -> None:
    with pytest.raises(IsADirectoryError, match="目录"):
        sxio.remove(None, env.game / "sub")
    assert (env.game / "sub").is_dir()
    s = env.make()
    with pytest.raises(IsADirectoryError, match="目录"):
        sxio.remove(s, env.game / "sub")
    assert "sub" not in s.changes  # 未误记 tombstone


def test_copy_path_direct_mode_copies_tree(tmp_path: Path) -> None:
    src = tmp_path / "save_slot"
    (src / "nested").mkdir(parents=True)
    (src / "save.bin").write_bytes(b"SAVE")
    (src / "nested" / "meta.txt").write_text("meta", encoding="utf-8")
    dst = tmp_path / "backup" / "save_slot"

    ret = sxio.copy_path(None, src, dst)

    assert ret == dst
    assert (dst / "save.bin").read_bytes() == b"SAVE"
    assert (dst / "nested" / "meta.txt").read_text(encoding="utf-8") == "meta"


def test_copy_path_sandbox_side_then_applies_tree(env) -> None:
    # 授权根内真实目录（含嵌套文件），备份到根外 side 区
    (env.game / "sub" / "deep").mkdir(parents=True)
    (env.game / "sub" / "keep.txt").write_text("keep", encoding="utf-8")
    (env.game / "sub" / "deep" / "inner.bin").write_bytes(b"INNER")
    outside = env.game.parent / "outbak" / "sub"

    s = env.make()
    sxio.copy_path(s, env.game / "sub", outside,
                   side_dst=True, pristine_src=True)
    # apply 前真实根外目标不存在
    assert not outside.exists()
    assert list(s.side_dir.rglob("inner.bin"))
    s.seal()
    results = s.apply_changes()

    assert all(item["ok"] for item in results), results
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (outside / "deep" / "inner.bin").read_bytes() == b"INNER"


def test_save_backup_includes_directory_saves(env) -> None:
    # 「一个存档槽一个目录」型存档不再被静默漏掉
    slot = env.saves / "slot_dir"
    (slot / "deep").mkdir(parents=True)
    (slot / "slot.sav").write_bytes(b"DIR-SAVE")
    (slot / "deep" / "meta.bin").write_bytes(b"META")

    s = env.make()
    agent = GameContentAgent()
    agent.initialize()
    r = asyncio_run(agent.execute(AgentTask(
        name="save_management", agent="game_content",
        data={"game_name": "GameY", "op": "saves",
              "save_path": str(env.saves), "_sandbox_session": s})))
    assert r.success, r.message
    assert list(s.side_dir.rglob("slot.sav"))
    assert list(s.side_dir.rglob("meta.bin"))
    assert any("slot_dir" in c.real_path and c.op == "side_write"
               for c in s.changes.values())


def test_script_delete_directory_rejected(env) -> None:
    # 直接模式：目录不能走"备份后删文件"链路，且磁盘不受影响
    ed = _editor()
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "sub", "operation": "delete"})))
    assert r.success is False
    assert "目录" in r.message
    assert (env.game / "sub").is_dir()

    # 沙箱模式同样前置拒绝
    s = env.make()
    r = asyncio_run(ed.execute(_task({
        "game_dir": str(env.game), "file": "sub",
        "operation": "delete", "_sandbox_session": s})))
    assert r.success is False
    assert "目录" in r.message
    assert (env.game / "sub").is_dir()


def test_web_download_to_directory_rejected(env) -> None:
    agent = WebSearchAgent()
    agent.initialize()
    r = asyncio_run(agent.execute(_task({
        "game_dir": str(env.game), "url": "https://example.com/x.bin",
        "file": "sub"})))
    assert r.success is False
    assert "目录" in r.message
    assert (env.game / "sub").is_dir()
