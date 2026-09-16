"""文件扫描 + 平台识别 单测 — 用临时目录构造真实文件结构验证。

覆盖：
- BepInEx + MelonLoader 共存冲突检测
- 零字节文件检测
- Unity 关键文件缺失检测
- GOG 特征（goggame-*.info）识别
- Steam appmanifest 识别（含 AppID 解析）
- 无特征目录兜底为 standalone
"""

from __future__ import annotations

from pathlib import Path

from gamedoctor.analyzer.files import DefaultFileScanner
from gamedoctor.fingerprint.platform import DefaultPlatformAdapter
from gamedoctor.models import (
    EngineFingerprint,
    PlatformInfo,
    RuntimeFingerprint,
    TechStackFingerprint,
)


def _fp(root: Path, engine: str = "") -> TechStackFingerprint:
    return TechStackFingerprint(
        game_name="TestGame",
        engine=EngineFingerprint(engine=engine) if engine else None,
        runtime=RuntimeFingerprint(),
        platform=PlatformInfo(platform="standalone", install_path=root),
    )


def test_mod_loader_conflict(tmp_path: Path):
    """BepInEx 与 MelonLoader 共存必须报冲突。"""
    (tmp_path / "BepInEx").mkdir()
    (tmp_path / "MelonLoader").mkdir()
    (tmp_path / "winhttp.dll").write_bytes(b"x")

    issues = DefaultFileScanner().scan(_fp(tmp_path))
    assert any("BepInEx" in i and "MelonLoader" in i for i in issues), issues


def test_zero_byte_file_detected(tmp_path: Path):
    """零字节文件（下载中断特征）应被报告。"""
    (tmp_path / "game.exe").write_bytes(b"OK")
    (tmp_path / "broken.pak").write_bytes(b"")

    issues = DefaultFileScanner().scan(_fp(tmp_path))
    assert any("零字节" in i for i in issues), issues


def test_unity_keyfile_missing(tmp_path: Path):
    """Unity 游戏缺 Assembly-CSharp.dll 报安装不完整。"""
    data = tmp_path / "TestGame_Data"
    data.mkdir()
    (data / "Managed").mkdir()
    # 故意不创建 Assembly-CSharp.dll
    issues = DefaultFileScanner().scan(_fp(tmp_path, engine="unity"))
    assert any("Assembly-CSharp.dll" in i for i in issues), issues


def test_clean_dir_no_issues(tmp_path: Path):
    """正常目录（只有正常文件）不报问题。"""
    (tmp_path / "game.exe").write_bytes(b"MZ" + b"\0" * 100)
    (tmp_path / "config.ini").write_text("a=1", encoding="utf-8")
    issues = DefaultFileScanner().scan(_fp(tmp_path))
    assert issues == [], issues


def test_platform_gog_detection(tmp_path: Path):
    """目录内有 goggame-<id>.info → GOG。"""
    (tmp_path / "goggame-1207658926.info").write_text("{}", encoding="utf-8")
    info = DefaultPlatformAdapter().detect("Some Game", tmp_path)
    assert info.platform == "gog"
    assert info.app_id == "1207658926"
    assert info.install_path == tmp_path


def test_platform_steam_manifest(tmp_path: Path):
    """steamapps/common/<game> + appmanifest 名称匹配 → Steam，带 AppID。"""
    steamapps = tmp_path / "steamapps"
    game_dir = steamapps / "common" / "Cyberpunk 2077"
    game_dir.mkdir(parents=True)
    (game_dir / "Cyberpunk2077.exe").write_bytes(b"x")
    (steamapps / "appmanifest_1091500.acf").write_text(
        '"AppState"\n{\n\t"appid"\t"1091500"\n\t"name"\t"Cyberpunk 2077"\n'
        '\t"installdir"\t"Cyberpunk 2077"\n}\n',
        encoding="utf-8",
    )

    info = DefaultPlatformAdapter().detect("Cyberpunk 2077", game_dir)
    assert info.platform == "steam"
    assert info.app_id == "1091500"
    assert info.install_path == game_dir


def test_platform_standalone_fallback(tmp_path: Path):
    """无任何平台特征 → standalone，安装路径原样返回。"""
    (tmp_path / "game.exe").write_bytes(b"x")
    info = DefaultPlatformAdapter().detect("Unknown Indie Game", tmp_path)
    assert info.platform == "standalone"
    assert info.install_path == tmp_path


def test_scan_without_install_dir(tmp_path: Path):
    """install_path 为 None 时扫描返回空清单，不抛异常。"""
    fp = TechStackFingerprint(game_name="X", platform=PlatformInfo(platform="standalone"))
    assert DefaultFileScanner().scan(fp) == []
