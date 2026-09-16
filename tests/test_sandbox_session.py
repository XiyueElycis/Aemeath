"""SandboxSession / sandbox.io 核心语义测试。

覆盖：写入隔离零落盘、路径穿越拒绝、读透传与 tombstone 遮蔽、apply/discard
含备份、direct（session=None）直通、大目录初始化零复制、持久化往返。
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from pathlib import Path

import pytest

from gamedoctor.sandbox import SandboxSession, SandboxViolation
from gamedoctor.sandbox.session import ChangeOp, SandboxStatus
from gamedoctor.sandbox import io as sxio


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造：真实游戏目录 + 隔离的沙箱/备份根（不碰用户 ~/.gamedoctor）。"""
    apphome = tmp_path / "apphome"
    monkeypatch.setattr("gamedoctor.sandbox.session.app_dir", lambda: apphome)

    game = tmp_path / "game"
    (game / "sub").mkdir(parents=True)
    (game / "config.ini").write_text("[main]\nkey=old\n", encoding="utf-8")
    (game / "sub" / "keep.txt").write_text("keep-me", encoding="utf-8")
    (game / "to_delete.txt").write_text("bye", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()

    def make(ticket: str = "T-1", root: Path | None = None) -> SandboxSession:
        return SandboxSession(ticket, root or game, sandbox_base=apphome / "sandbox")

    return SimpleNamespace(game=game, outside=outside, apphome=apphome, make=make)


def snapshot(root: Path) -> dict[str, str]:
    """目录树内容哈希清单（相对路径 → sha256）。"""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


# --------------------------------------------------------------------- #
# TR-1.1 写入隔离 + 变更清单
# --------------------------------------------------------------------- #
def test_write_operations_never_touch_real_tree(env) -> None:
    before = snapshot(env.game)
    s = env.make()

    # modify 已有文件
    sxio.write_text(s, env.game / "config.ini", "[main]\nkey=new\n")
    # create 新文件
    sxio.write_text(s, "new.lua", "print('hi')")
    # replace：用根内真实文件覆盖另一个文件
    sxio.replace_file(s, env.game / "sub" / "keep.txt", env.game / "config.ini")
    # delete
    sxio.remove(s, "to_delete.txt")
    # download（显式 op）
    sxio.write_bytes(s, "pkg.bin", b"PK\x00\x03", op=ChangeOp.DOWNLOAD, is_text=False)
    # side：根外备份输出（模拟智能体自发备份）
    sxio.copy_file(s, env.game / "config.ini",
                   env.outside / "bak" / "config.ini.bak",
                   side_dst=True, pristine_src=True)

    # 真实目录逐字节不变
    assert snapshot(env.game) == before

    # overlay 产物齐全
    assert (s.overlay_dir / "config.ini").is_file()
    assert (s.overlay_dir / "new.lua").is_file()
    assert (s.overlay_dir / "pkg.bin").is_file()
    # side 产物在沙箱内，真实根外目标不存在
    assert env.outside.joinpath("bak", "config.ini.bak").exists() is False
    side_files = list(s.side_dir.rglob("*.bak"))
    assert len(side_files) == 1

    # 变更清单
    ops = {c.relpath: c.op for c in s.changes.values()}
    assert ops["config.ini"] == ChangeOp.MODIFY
    assert ops["new.lua"] == ChangeOp.CREATE
    assert ops["to_delete.txt"] == ChangeOp.DELETE
    assert ops["pkg.bin"] == ChangeOp.DOWNLOAD
    assert any(op == ChangeOp.SIDE_WRITE for op in ops.values())
    assert "to_delete.txt" in s.tombstones

    # meta.json 已落盘且可重新加载
    assert (s.root / "meta.json").is_file()


# --------------------------------------------------------------------- #
# TR-1.2 路径穿越 / 跨根拒绝
# --------------------------------------------------------------------- #
def test_path_traversal_and_cross_root_rejected(env) -> None:
    s = env.make()

    with pytest.raises(SandboxViolation):
        sxio.write_text(s, "../evil.txt", "x")
    with pytest.raises(SandboxViolation):
        sxio.write_text(s, "sub/../../evil.txt", "x")
    with pytest.raises(SandboxViolation):
        sxio.write_bytes(s, env.outside / "x.txt", b"x")
    with pytest.raises(SandboxViolation):
        sxio.remove(s, "../evil.txt")

    # 攻击尝试后盘上无产物
    assert not (env.game.parent / "evil.txt").exists()
    assert not (env.outside / "x.txt").exists()


# --------------------------------------------------------------------- #
# TR-1.3 读透传 / overlay 优先 / tombstone 遮蔽
# --------------------------------------------------------------------- #
def test_read_passthrough_overlay_and_tombstone(env) -> None:
    s = env.make()
    sxio.write_text(s, "config.ini", "NEW")
    sxio.remove(s, "to_delete.txt")

    assert sxio.read_text(s, "config.ini") == "NEW"            # overlay 命中
    assert sxio.read_text(s, "sub/keep.txt") == "keep-me"      # 真实透传
    assert not sxio.exists(s, "to_delete.txt")                 # tombstone
    with pytest.raises(FileNotFoundError):
        sxio.read_bytes(s, "to_delete.txt")


def test_list_dir_merged_view(env) -> None:
    s = env.make()
    sxio.write_text(s, "created_in_sb.lua", "x")
    sxio.remove(s, "to_delete.txt")

    names = {e["name"] for e in sxio.list_dir(s, env.game)}
    assert "created_in_sb.lua" in names
    assert "to_delete.txt" not in names
    assert "config.ini" in names

    # direct 口径：原生 iterdir 字段
    direct = sxio.list_dir(None, env.game)
    assert {e["name"] for e in direct} >= {"config.ini", "sub"}


def test_create_then_delete_is_net_zero(env) -> None:
    s = env.make()
    sxio.write_text(s, "ghost.lua", "x")
    sxio.remove(s, "ghost.lua")
    assert "ghost.lua" not in s.changes
    assert not (s.overlay_dir / "ghost.lua").exists()
    assert not (env.game / "ghost.lua").exists()


# --------------------------------------------------------------------- #
# TR-1.4 apply / discard
# --------------------------------------------------------------------- #
def test_seal_apply_with_backup_and_side_writeback(env) -> None:
    s = env.make()
    sxio.write_text(s, "config.ini", "[main]\nkey=applied\n")
    sxio.write_text(s, "new.lua", "return 1")
    sxio.remove(s, "to_delete.txt")
    sxio.copy_file(s, env.game / "sub" / "keep.txt",
                   env.outside / "bak" / "keep.txt.bak",
                   side_dst=True, pristine_src=True)
    s.seal()
    assert s.status == SandboxStatus.READY

    results = s.apply_changes()

    assert all(item["ok"] for item in results), results
    assert (env.game / "config.ini").read_text(encoding="utf-8") == \
        "[main]\nkey=applied\n"
    assert (env.game / "new.lua").read_text(encoding="utf-8") == "return 1"
    assert not (env.game / "to_delete.txt").exists()
    # side 产物写回真实原位
    assert (env.outside / "bak" / "keep.txt.bak").is_file()
    # 修改/删除原件已备份，create/side 不备份
    backup_root = env.apphome / "backups" / "apply_T-1"
    backed = {p.name for p in backup_root.rglob("*") if p.is_file()}
    assert any(n.endswith("config.ini") for n in backed)
    assert any(n.endswith("to_delete.txt") for n in backed)
    assert not any(n.endswith("new.lua") for n in backed)
    assert s.status == SandboxStatus.APPLIED

    # 持久化往返
    again = SandboxSession.load("T-1", sandbox_base=env.apphome / "sandbox")
    assert again.status == SandboxStatus.APPLIED
    assert SandboxSession.list_sessions(
        env.apphome / "sandbox", status=SandboxStatus.APPLIED)[0]["ticket_id"] == "T-1"


def test_discard_leaves_zero_change(env) -> None:
    before = snapshot(env.game)
    s = env.make("T-2")
    sxio.write_text(s, "config.ini", "CHANGED")
    sxio.write_text(s, "another.lua", "x")
    s.seal()
    s.discard()

    assert snapshot(env.game) == before
    assert not s.root.exists()
    assert SandboxSession.list_sessions(env.apphome / "sandbox") == []


def test_apply_running_session_rejected(env) -> None:
    s = env.make("T-3")
    with pytest.raises(Exception):
        s.apply_changes()


# --------------------------------------------------------------------- #
# TR-1.5 session=None 直通
# --------------------------------------------------------------------- #
def test_direct_io_matches_native_semantics(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    sxio.write_text(None, f, "hello")
    assert f.read_text(encoding="utf-8") == "hello"
    assert sxio.read_text(None, f) == "hello"
    assert sxio.exists(None, f)
    d = tmp_path / "d"
    sxio.ensure_dir(None, d)
    assert d.is_dir()
    sxio.write_bytes(None, tmp_path / "b.bin", b"\x00\x01")
    sxio.copy_file(None, f, tmp_path / "a-copy.txt")
    assert (tmp_path / "a-copy.txt").read_bytes() == b"hello"
    sxio.replace_file(None, f, tmp_path / "a-copy.txt")
    assert f.read_bytes() == b"hello"
    sxio.remove(None, f)
    assert not f.exists()


# --------------------------------------------------------------------- #
# TR-1.6 大目录初始化零复制
# --------------------------------------------------------------------- #
def test_init_does_not_copy_real_tree(tmp_path: Path, monkeypatch) -> None:
    apphome = tmp_path / "apphome"
    monkeypatch.setattr("gamedoctor.sandbox.session.app_dir", lambda: apphome)
    game = tmp_path / "biggame"
    (game / "assets").mkdir(parents=True)
    for i in range(1000):
        (game / "assets" / f"f{i:04d}.bin").write_bytes(b"x" * 32)

    s = SandboxSession("BIG", game, sandbox_base=apphome / "sandbox")

    assert list(s.overlay_dir.rglob("*")) == []
    assert list(s.side_dir.rglob("*")) == []
    assert s.changes == {}
