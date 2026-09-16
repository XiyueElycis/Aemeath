"""context_builder 单测 —— 指纹三件套接入治理链路的采集主干。

外部进程（PowerShell / nvidia-smi）与平台库扫描全部 monkeypatch，
测试在任何机器上结果一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gamedoctor import context_builder as cb
from gamedoctor.models import (
    EngineFingerprint,
    PlatformInfo,
    RuntimeFingerprint,
)


@pytest.fixture
def fake_runtime() -> RuntimeFingerprint:
    return RuntimeFingerprint(
        os="Windows 11",
        arch="AMD64",
        directx_version="12",
        dotnet="4.8",
        vc_components=["vc2015-2022_x64"],
        gpu="NVIDIA GeForce RTX 4070",
        gpu_driver="32.0.15.6094",
        gpu_api="DirectX 12",
    )


@pytest.fixture
def _fake_vram(monkeypatch):
    monkeypatch.setattr(cb, "probe_gpu_vram_mb", lambda gpu_name=None: 12288)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, fake_runtime):
    """隔离所有机器/环境相关探测，默认给出稳定值。"""
    cb.probe_gpu_vram_mb.cache_clear()
    monkeypatch.setattr(cb, "cached_runtime", lambda: fake_runtime)
    monkeypatch.setattr(cb, "detect_platform",
                        lambda name, root=None: PlatformInfo(platform="standalone"))
    yield
    # monkeypatch 此刻尚未撤销，可能还是替身 lambda，做属性判断
    getattr(cb.probe_gpu_vram_mb, "cache_clear", lambda: None)()


def _make_unity_game(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "UnityPlayer.dll").write_bytes(b"unity" * 32)
    (root / "TestGame.exe").write_bytes(b"MZ" + b"\0" * 1024)
    (root / "TestGame_Data").mkdir()
    return root


def test_build_context_unity_dir(tmp_path: Path, _fake_vram):
    game = _make_unity_game(tmp_path / "TestGame")

    ctx, fp = cb.build_context("TestGame", str(game))

    assert ctx.fingerprint_ready is True
    assert ctx.engine == "unity"
    assert ctx.gpu_name == "NVIDIA GeForce RTX 4070"
    assert ctx.gpu_vram_mb == 12288
    assert ctx.directx_version == "12"
    assert ctx.dotnet_version == "4.8"
    assert ctx.vc_components == ["vc2015-2022_x64"]
    assert ctx.os_name == "Windows 11"
    assert ctx.os_arch == "AMD64"
    assert str(ctx.install_path) == str(game)

    # 指纹 dict 必须可 JSON 序列化（要随 task.data 跨 HTTP 边界）
    json.dumps(fp, ensure_ascii=False)
    assert fp["engine"] == "unity"
    assert fp["gpu_vram_mb"] == 12288
    assert fp["fingerprint_ready"] is True
    assert fp["game_dir"] == str(game)
    assert Path not in {type(v) for v in fp.values()}


def test_platform_install_path_is_authoritative(tmp_path: Path, monkeypatch, _fake_vram):
    """Steam/Epic appmanifest 权威识别出的目录优先于手输目录。"""
    typed = _make_unity_game(tmp_path / "TypedName")
    authoritative = tmp_path / "steamapps" / "common" / "Real Game Name"
    authoritative.mkdir(parents=True)
    (authoritative / "UnityPlayer.dll").write_bytes(b"x")
    monkeypatch.setattr(
        cb, "detect_platform",
        lambda name, root=None: PlatformInfo(
            platform="steam", app_id="123450", install_path=authoritative,
        ),
    )

    ctx, fp = cb.build_context("TestGame", str(typed))

    assert ctx.platform == "steam"
    assert ctx.app_id == "123450"
    assert ctx.install_path == authoritative
    # 原始手输目录保留在指纹里可审计
    assert fp["game_dir"] == str(typed)
    assert fp["platform_install_path"] == str(authoritative)


def test_engine_detect_failure_degrades(tmp_path: Path, monkeypatch, _fake_vram):
    """引擎识别抛异常不得阻断上下文构建。"""
    def _boom(_path):
        raise OSError("扫描失败")

    monkeypatch.setattr(cb, "detect_engine", _boom)
    game = tmp_path / "TestGame"
    game.mkdir()

    ctx, fp = cb.build_context("TestGame", str(game))

    assert ctx.fingerprint_ready is True
    assert ctx.engine is None
    assert fp["engine"] is None
    assert fp["gpu"] == "NVIDIA GeForce RTX 4070"  # 其余探测不受影响


def test_no_game_dir_does_not_touch_engine_scan(monkeypatch, _fake_vram):
    """无目录（闲聊/纯问答）构建：不做引擎扫描，平台按 standalone。"""
    called = {"engine": False}
    monkeypatch.setattr(cb, "detect_engine",
                        lambda p: called.__setitem__("engine", True))

    ctx, fp = cb.build_context("随便聊聊", "")

    assert called["engine"] is False
    assert ctx.engine is None
    assert ctx.install_path is None
    assert fp["platform"] == "standalone"
    json.dumps(fp)  # 仍可序列化


def test_dir_size_mb(tmp_path: Path):
    payload = tmp_path / "big.bin"
    payload.write_bytes(b"a" * (2 * 1024 * 1024))  # 恰好 2 MiB
    assert cb._dir_size_mb(tmp_path) == 2


def test_vram_none_when_commands_unavailable(monkeypatch):
    """nvidia-smi 与 CIM 都取不到 → None（未知），绝不返回 0。"""
    monkeypatch.setattr(cb, "_run_cmd", lambda args, timeout=8.0: None)
    assert cb.probe_gpu_vram_mb("NVIDIA GeForce RTX 4070") is None


def test_vram_cim_overflow_distrusted(monkeypatch):
    """AdapterRAM 的 uint32 溢出值（≥4GiB 边界）不可信 → None；小显存正常换算。"""
    csv_over = (
        '"Name","AdapterRAM"\r\n'
        '"NVIDIA GeForce RTX 4070","4294443008"\r\n'
    )
    monkeypatch.setattr(cb, "_vram_from_nvidia_smi", lambda: None)
    monkeypatch.setattr(cb, "_run_cmd", lambda args, timeout=8.0: csv_over)
    assert cb.probe_gpu_vram_mb("NVIDIA GeForce RTX 4070") is None

    csv_ok = (
        '"Name","AdapterRAM"\r\n'
        '"Intel(R) UHD Graphics 630","2147483648"\r\n'  # 2 GiB
    )
    monkeypatch.setattr(cb, "_run_cmd", lambda args, timeout=8.0: csv_ok)
    assert cb.probe_gpu_vram_mb("Intel(R) UHD Graphics 630") == 2048


def test_empty_fingerprint_is_lightweight():
    fp = cb.empty_fingerprint()
    assert fp["fingerprint_ready"] is False
    assert fp["platform"] == "standalone"
    json.dumps(fp)
