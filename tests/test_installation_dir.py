"""installation 智能体单测 —— 尊重传入目录、三策略找 exe、平台真实探测。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gamedoctor.agents.base_agent import AgentTask
from gamedoctor.agents.installation import installation_agent as ia
from gamedoctor.agents.installation.installation_agent import InstallationAgent
from gamedoctor.models import GameContext, PlatformInfo


@pytest.fixture
def agent(monkeypatch):
    a = InstallationAgent()
    a._load_path_mappings()
    # 平台探测默认 standalone，不触碰真机 Steam/Epic 清单
    monkeypatch.setattr(
        ia, "detect_platform",
        lambda name, root=None: PlatformInfo(platform="standalone"),
    )
    return a


def _task(game_dir: str = "", ctx: GameContext | None = None,
          source: str = "auto") -> AgentTask:
    return AgentTask(
        name="installation",
        context=ctx,
        data={"game_name": Path(game_dir).name if game_dir else "TestGame",
              "game_dir": game_dir, "source": source},
    )


def test_existing_game_dir_returned_as_is(tmp_path: Path, agent):
    """批 4 核心回归：存在的 game_dir 必须原样使用，绝不重算到 ~/Games。"""
    game = tmp_path / "TestGame"
    game.mkdir()
    (game / "TestGame.exe").write_bytes(b"MZ" + b"\0" * 2048)

    result = asyncio.run(agent.execute(_task(str(game))))
    assert result.success is True
    plan = result.data
    assert plan["install_path"] == str(game)
    assert plan["existing_game"] is True
    # 找到目录同名 exe 且验证通过
    assert plan["verification"]["success"] is True
    checks_text = " ".join(plan["verification"]["checks"])
    assert "TestGame.exe" in checks_text
    # standalone 且自动模式须注明平台归属未确认
    assert any("平台归属" in c for c in plan["pre_install_checks"])


def test_context_install_path_overrides_data(agent, tmp_path: Path, monkeypatch):
    real = tmp_path / "RealGame"
    real.mkdir()
    (real / "RealGame.exe").write_bytes(b"MZ")
    ctx = GameContext(game_name="TypedName", install_path=real)
    task = _task(str(tmp_path / "TypedName"), ctx=ctx)

    result = asyncio.run(agent.execute(task))
    assert result.data["install_path"] == str(real)


def test_finder_same_name_exe(tmp_path: Path, agent):
    # 目录同名 exe 优先于同目录内更大的其它 exe
    game = tmp_path / "MyGame"
    game.mkdir()
    (game / "MyGame.exe").write_bytes(b"MZ")
    (game / "other_tool.exe").write_bytes(b"x" * 8192)
    found = agent._find_main_executable(game)
    assert found is not None and found.name == "MyGame.exe"


def test_finder_fixed_name_fallback(tmp_path: Path, agent):
    # 没有同名 exe 时用固定名
    (tmp_path / "launcher.exe").write_bytes(b"MZ")
    found = agent._find_main_executable(tmp_path)
    assert found is not None and found.name == "launcher.exe"


def test_finder_largest_exe_excludes_installers(tmp_path: Path, agent):
    # 只有安装器/卸载器 + 一个真正的游戏 exe（体积更大也无妨，排除器靠关键词）
    (tmp_path / "unins000.exe").write_bytes(b"x" * 8192)
    (tmp_path / "vcredist_x64.exe").write_bytes(b"x" * 4096)
    (tmp_path / "setup.exe").write_bytes(b"x" * 4096)
    real = tmp_path / "GameData.exe"
    real.write_bytes(b"MZ" * 100)
    found = agent._find_main_executable(tmp_path)
    assert found == real


def test_finder_returns_none_without_exe(tmp_path: Path, agent):
    (tmp_path / "readme.txt").write_text("hi")
    assert agent._find_main_executable(tmp_path) is None


def test_verify_fails_when_no_exe(tmp_path: Path, agent):
    game = tmp_path / "EmptyGame"
    game.mkdir()
    result = asyncio.run(agent.execute(_task(str(game))))
    assert result.success is True
    assert result.data["verification"]["success"] is False
    assert any("未找到主程序" in i for i in result.data["verification"]["issues"])


def test_optimize_path_respects_existing_dir(tmp_path: Path, agent):
    game = tmp_path / "SomeGame"
    game.mkdir()
    assert agent._optimize_install_path("SomeGame", "steam", None,
                                        game_dir=str(game)) == game


def test_select_source_uses_detected_platform(agent):
    assert agent._select_source("G", "auto", "steam") == "steam"
    assert agent._select_source("G", "auto", "epic") == "epic"
    assert agent._select_source("G", "auto", "standalone") == "standalone"
    # 显式指定不被探测结果覆盖
    assert agent._select_source("G", "gog", "standalone") == "gog"


def test_platform_checks_real_detection(monkeypatch):
    a = InstallationAgent()
    monkeypatch.setattr(ia, "detect_platform",
                        lambda name, root=None: PlatformInfo(platform="steam",
                                                             app_id="123"))
    assert a._check_steam_game("Anything") is True
    assert a._check_epic_game("Anything") is False

    monkeypatch.setattr(ia, "detect_platform",
                        lambda name, root=None: PlatformInfo(platform="epic"))
    assert a._check_steam_game("Anything") is False
    assert a._check_epic_game("Anything") is True


def test_detect_platform_failure_degrades(monkeypatch):
    a = InstallationAgent()

    def _boom(name, root=None):
        raise OSError("清单读取失败")

    monkeypatch.setattr(ia, "detect_platform", _boom)
    info = a._detect_platform("G")
    assert info.platform == "standalone"
    assert a._check_steam_game("G") is False
