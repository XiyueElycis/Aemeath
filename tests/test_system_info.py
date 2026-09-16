"""system_info 改造单测 —— GPU/OS/DX 采集委托指纹层，死函数删除。

外部进程探测一律 monkeypatch；OS 注册表用例仅在 Windows 运行。
"""

from __future__ import annotations

import os

import pytest

from gamedoctor import context_builder as cb
from gamedoctor.models import RuntimeFingerprint
from gamedoctor.utils import system_info


@pytest.fixture
def runtime_factory(monkeypatch):
    """用可替换的 RuntimeFingerprint 顶替缓存探针。"""
    cb.probe_gpu_vram_mb.cache_clear()
    holder = {"rt": RuntimeFingerprint()}
    monkeypatch.setattr(cb, "cached_runtime", lambda: holder["rt"])
    yield holder
    getattr(cb.probe_gpu_vram_mb, "cache_clear", lambda: None)()


def test_gpu_info_from_fingerprint(runtime_factory, monkeypatch):
    runtime_factory["rt"] = RuntimeFingerprint(
        gpu="NVIDIA GeForce RTX 4070",
        gpu_driver="32.0.15.6094",
        gpu_api="DirectX 12",
    )
    monkeypatch.setattr(cb, "probe_gpu_vram_mb", lambda name=None: 12288)

    gpus = system_info.get_gpu_info()
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu.name == "NVIDIA GeForce RTX 4070"
    assert gpu.driver_version == "32.0.15.6094"
    assert gpu.manufacturer == "NVIDIA"
    assert gpu.memory == 12288
    assert "DirectX 12" in gpu.api_support


def test_gpu_info_unknown_when_probe_fails(monkeypatch):
    def _boom():
        raise OSError("CIM 不可用")

    monkeypatch.setattr(cb, "cached_runtime", _boom)
    gpus = system_info.get_gpu_info()
    assert len(gpus) == 1
    assert gpus[0].name == "Unknown GPU"
    assert gpus[0].memory == 0


def test_directx_info_from_fingerprint(runtime_factory):
    runtime_factory["rt"] = RuntimeFingerprint(
        directx_version="12 Ultimate", directx_legacy=True
    )
    info = system_info.get_directx_info()
    assert info["version"] == "12 Ultimate"
    assert any("DirectX" in f for f in info["installed_features"])
    assert info["legacy_runtime_complete"] is True


def test_directx_info_unknown_without_probe(monkeypatch):
    monkeypatch.setattr(cb, "cached_runtime",
                        lambda: RuntimeFingerprint(directx_version=None,
                                                   directx_legacy=None))
    info = system_info.get_directx_info()
    assert info["version"] == "Unknown"
    assert info["installed_features"] == []
    assert info["legacy_runtime_complete"] is None


@pytest.mark.skipif(os.name != "nt", reason="Windows 注册表用例")
def test_os_info_uses_build_authority():
    """Win11 上不得再报遗留的 Windows 10 / 6.3；build 必须是真实数字。"""
    info = system_info.get_os_info()
    assert info.name.startswith("Windows")
    assert info.build.isdigit()
    assert info.version.startswith("10.0.")
    if int(info.build[:5]) >= 22000:
        assert "11" in info.name
    assert info.architecture  # 非空


def test_dead_requirements_check_removed():
    """含 self NameError 的死函数及其恒 True 助手必须已删除。"""
    for name in ("get_system_requirements_check", "_is_cpu_compatible",
                 "_is_gpu_compatible", "_is_directx_compatible"):
        assert not hasattr(system_info, name), f"{name} 应已删除"


def test_get_system_info_shape(runtime_factory, monkeypatch):
    """聚合结构保持旧契约：gpu 为 dict 列表、directx/os 键齐全。"""
    runtime_factory["rt"] = RuntimeFingerprint(
        gpu="AMD Radeon RX 7900", gpu_driver="1.2.3",
        directx_version="12",
    )
    monkeypatch.setattr(cb, "probe_gpu_vram_mb", lambda name=None: None)
    data = system_info.get_system_info()
    assert {"cpu", "gpu", "memory", "disks", "os", "directx"} <= set(data)
    assert data["gpu"][0]["name"] == "AMD Radeon RX 7900"
    assert data["directx"]["version"] == "12"
