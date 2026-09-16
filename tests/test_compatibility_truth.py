"""兼容性判定真实性单测 —— 注入式硬件画像，不触碰真机探测。

回归目标：Win11 + RTX 4070 不得再出 critical；未知数据不表态；
CPU 代际、显存、OS build、引擎档位各自按真实规则比较。
"""

from __future__ import annotations

import asyncio

import pytest

from gamedoctor.agents.base_agent import AgentTask
from gamedoctor.agents.compatibility import compatibility_agent as ca
from gamedoctor.agents.compatibility.compatibility_agent import (
    CompatibilityAgent,
    _parse_cpu_tier,
    _parse_gpu_tier,
    _leading_number,
)
from gamedoctor.models import GameContext


def _system_info(cpu="Intel(R) Core(TM) i7-12700", gpu="NVIDIA GeForce RTX 4070",
                 gpu_mem=12288, ram_gb=32, os_name="Windows 11",
                 build="22631", dx="12", net="4.8.1"):
    return {
        "cpu": {"name": cpu},
        "gpu": [{"name": gpu, "memory": gpu_mem}],
        "memory": {"total": int(ram_gb * 1024 ** 3)},
        "os": {"name": os_name, "build": build},
        "directx": {"version": dx},
        "net_framework": {"version": net},
    }


@pytest.fixture
def agent(monkeypatch):
    a = CompatibilityAgent()
    a._load_known_issues()
    # 默认高端画像；用例可覆盖 monkeypatch
    monkeypatch.setattr(ca, "get_system_info_safe", lambda: _system_info())
    return a


# --------------------------------------------------------------------- 型号解析

def test_parse_cpu_tier_intel():
    assert _parse_cpu_tier("Intel(R) Core(TM) i7-12700K") == ("intel", 7, 12)
    assert _parse_cpu_tier("i5-7500") == ("intel", 5, 7)
    assert _parse_cpu_tier("i5-750") == ("intel", 5, 1)   # 3 位型号 = 1 代酷睿
    assert _parse_cpu_tier("AMD Ryzen 5 3600 6-Core") == ("amd", 5, 3)
    assert _parse_cpu_tier("Ryzen 7 7800X3D") == ("amd", 7, 7)
    assert _parse_cpu_tier("") is None
    assert _parse_cpu_tier("Unknown CPU") is None


def test_parse_gpu_tier():
    assert _parse_gpu_tier("NVIDIA GeForce RTX 4070") == ("nvidia", 2, 4070)
    assert _parse_gpu_tier("NVIDIA GeForce GTX 970") == ("nvidia", 1, 970)
    assert _parse_gpu_tier("AMD Radeon RX 7900 XTX") == ("amd", 1, 7900)
    assert _parse_gpu_tier("Intel(R) UHD Graphics 630")[0] == "intel_igpu"
    assert _parse_gpu_tier("Some Weird Adapter") is None


def test_leading_number():
    assert _leading_number("12 Ultimate") == 12.0
    assert _leading_number("11") == 11.0
    assert _leading_number("Unknown") is None
    assert _leading_number(None) is None


# --------------------------------------------------------------------- 主判定

def test_modern_machine_unity_zero_critical(agent):
    """批 3 核心回归：RTX4070/Win11/12GB 显存 + unity 档位必须 0 critical。"""
    result = agent._check_compatibility(
        "SomeUnityGame", _system_info(), [{"name": "NVIDIA GeForce RTX 4070",
                                           "memory": 12288}],
        engine="unity",
        game_context=GameContext(
            game_name="SomeUnityGame", engine="unity",
            gpu_name="NVIDIA GeForce RTX 4070", gpu_vram_mb=12288,
        ),
    )
    assert result.critical_issues == [], result.critical_issues
    assert result.overall_score == 100


def test_unknown_hardware_does_not_falsely_fail(agent, monkeypatch):
    """CPU/GPU 型号未知、显存 None 时不得凭空判不合格（漏报优于误报）。"""
    monkeypatch.setattr(ca, "get_system_info_safe",
                        lambda: _system_info(cpu="", gpu="Unknown GPU", gpu_mem=0))
    result = agent._check_compatibility(
        "X", _system_info(cpu="", gpu="Unknown GPU", gpu_mem=0),
        [{"name": "Unknown GPU", "memory": 0}],
        engine="unity",
        game_context=GameContext(game_name="X", engine="unity",
                                 gpu_name=None, gpu_vram_mb=None),
        fingerprint={"gpu": None, "gpu_vram_mb": None},
    )
    hw_critical = [i for i in result.critical_issues
                   if "CPU" in i or "GPU" in i or "内存" in i]
    assert hw_critical == [], result.critical_issues


def test_win7_fails_unity_os_tier(agent, monkeypatch):
    """Win7(build 7601) 跑 unity 档位（要求 build≥10240）必须判 OS 不合格。"""
    win7 = _system_info(os_name="Windows 7", build="7601", dx="11")
    result = agent._check_compatibility(
        "OldGame", win7, [{"name": "Unknown GPU", "memory": 0}],
        engine="unity", game_context=GameContext(engine="unity", gpu_vram_mb=None),
        fingerprint={"gpu_vram_mb": None},
    )
    os_issues = [i for i in result.critical_issues if "build" in i or "Windows" in i]
    assert os_issues, result.critical_issues
    # GPU 未知不得陪绑
    assert not any("GPU" in i for i in result.critical_issues)


def test_cpu_generation_comparison(agent):
    assert agent._is_cpu_compatible("Intel Core i5-6500", "i5-2500") is True
    assert agent._is_cpu_compatible("Intel Core i5-2400", "i5-3570") is False
    assert agent._is_cpu_compatible("i3-8100", "i5-2500") is False  # 系列档低
    assert agent._is_cpu_compatible("i7-4770", "i5-7500") is True   # i7 系列高于 i5
    assert agent._is_cpu_compatible("i3-4130", "i5-7500") is False


def test_gpu_vram_and_model_comparison(agent):
    # 型号相同，显存达标 / 不达标
    assert agent._is_gpu_compatible("GTX 750 Ti", 2048, "GTX 750 Ti", 2048) is True
    assert agent._is_gpu_compatible("GTX 750 Ti", 1024, "GTX 750 Ti", 2048) is False
    # 显存未知 → 不因显存判死
    assert agent._is_gpu_compatible("GTX 750 Ti", None, "GTX 750 Ti", 2048) is True
    # 需求未指定显存 → 只比型号
    assert agent._is_gpu_compatible("RTX 4070", None, "GTX 970", None) is True
    assert agent._is_gpu_compatible("GTX 650", 8192, "GTX 970", 4096) is False
    # 跨体系（核显 vs 独显需求）不硬比，显存够即过
    assert agent._is_gpu_compatible("Intel UHD Graphics 630", 4096,
                                    "GTX 970", 4096) is True


def test_engine_requirements_tiers(agent):
    assert agent._requirements_for("unreal")["ram"] == 16384
    assert agent._requirements_for("renpy")["dx"] is None
    assert agent._requirements_for("renpy")["ram"] == 4096
    assert agent._requirements_for(None)["gpu"] == "GTX 970"  # 未知引擎走兜底档
    unity = agent._requirements_for("unity")
    assert unity["vram"] == 2048  # 不再有 storage 错传给显存比较的问题


def test_directx_ultimate_string_parsed(agent):
    """'12 Ultimate' 必须解析为 12，而不是解析失败后静默跳过。"""
    info = _system_info(dx="12 Ultimate")
    result = agent._check_runtime_compatibility(info, agent._requirements_for("unity"))
    assert result["issues"] == []
    info_low = _system_info(dx="10")
    result_low = agent._check_runtime_compatibility(info_low, agent._requirements_for("unity"))
    assert any("DirectX" in i for i in result_low["issues"])


def test_unreal_engine_on_low_ram_flags(agent, monkeypatch):
    low = _system_info(ram_gb=8)
    result = agent._check_compatibility(
        "UEGame", low, [{"name": "NVIDIA GeForce RTX 4070", "memory": 12288}],
        engine="unreal",
        game_context=GameContext(engine="unreal", gpu_name="NVIDIA GeForce RTX 4070",
                                 gpu_vram_mb=12288),
    )
    # UE 档位要 16GB，8GB 必报内存
    assert any("内存" in i for i in result.critical_issues)
    # UE 高档位会带一条已知问题提示（引擎驱动，非游戏名关键词）
    assert result.known_issues


def test_execute_reads_fingerprint_from_task_data(monkeypatch):
    """execute 端到端：从 task.data['fingerprint'] 取引擎/显存，不依赖真机。"""
    monkeypatch.setattr(ca, "get_system_info_safe",
                        lambda: _system_info(gpu="Unknown GPU", gpu_mem=0))
    a = CompatibilityAgent()
    task = AgentTask(
        name="compatibility",
        data={
            "game_name": "FingerprintedGame",
            "fingerprint": {
                "engine": "unity",
                "gpu": "NVIDIA GeForce RTX 4070",
                "gpu_vram_mb": 12288,
                "game_dir": "",
            },
        },
    )
    result = asyncio.run(a.execute(task))
    assert result.success is True
    cr = result.data
    assert cr.system_requirements["vram"] == 2048
    assert cr.hardware_info["gpu_vram_mb"] == 12288
    assert cr.critical_issues == [], cr.critical_issues
