"""系统信息收集工具。

收集硬件和系统信息，供各智能体使用。
"""

from __future__ import annotations

import platform
import psutil
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# wmi 为 Windows 专用可选依赖；未安装时置 None，各函数内的 try/except 会
# 回退到 platform / psutil 实现，不影响在非 Windows 或未装 wmi 的环境运行。
try:
    import wmi
except ImportError:  # pragma: no cover
    wmi = None


@dataclass
class CPUInfo:
    """CPU 信息。"""
    name: str
    cores: int
    threads: int
    frequency: float
    architecture: str
    manufacturer: str


@dataclass
class GPUInfo:
    """GPU 信息。"""
    name: str
    memory: int
    driver_version: str
    manufacturer: str
    api_support: List[str]


@dataclass
class MemoryInfo:
    """内存信息。"""
    total: int
    available: int
    used: int
    percentage: float


@dataclass
class DiskInfo:
    """磁盘信息。"""
    total: int
    free: int
    used: int
    percentage: float
    type: str


@dataclass
class OSInfo:
    """操作系统信息。"""
    name: str
    version: str
    build: str
    architecture: str
    edition: str


def get_cpu_info() -> CPUInfo:
    """获取 CPU 信息。"""
    try:
        c = wmi.WMI()
        cpu = c.Win32_Processor()[0]

        # 获取核心数和线程数
        cores = cpu.NumberOfCores
        threads = cpu.NumberOfLogicalProcessors

        # 获取频率
        frequency = cpu.MaxClockSpeed / 1000  # MHz to GHz

        return CPUInfo(
            name=cpu.Name,
            cores=cores,
            threads=threads,
            frequency=frequency,
            architecture=cpu.AddressWidth,
            manufacturer=cpu.Manufacturer
        )
    except Exception:
        # 回退到 platform 模块
        return CPUInfo(
            name=platform.processor(),
            cores=psutil.cpu_count(logical=False) or 0,
            threads=psutil.cpu_count(logical=True) or 0,
            frequency=0.0,
            architecture=platform.machine(),
            manufacturer="Unknown"
        )


def get_gpu_info() -> List[GPUInfo]:
    """获取物理显卡信息。

    委托指纹层 :func:`gamedoctor.context_builder.cached_runtime`（PowerShell
    CIM 探测，已过滤向日葵/Parsec 等虚拟显示适配器），显存走
    :func:`probe_gpu_vram_mb`（nvidia-smi 优先，AdapterRAM 溢出值按未知处理）。

    注意：``GPUInfo.memory`` 为兼容旧聚合结构，未知时给 0；需要区分
    "未知 vs 0" 的判定层应直接读 ``GameContext.gpu_vram_mb``（None 即未知）。
    """
    from ..context_builder import cached_runtime, probe_gpu_vram_mb

    try:
        rt = cached_runtime()
        name = rt.gpu
    except Exception:
        name = None

    if not name:
        return [GPUInfo(
            name="Unknown GPU", memory=0, driver_version="Unknown",
            manufacturer="Unknown", api_support=[],
        )]

    low = name.lower()
    if any(k in low for k in ("nvidia", "geforce", "rtx", "gtx", "quadro")):
        manufacturer = "NVIDIA"
    elif any(k in low for k in ("amd", "radeon")):
        manufacturer = "AMD"
    elif any(k in low for k in ("intel", "arc", "iris")):
        manufacturer = "Intel"
    else:
        manufacturer = "Unknown"

    try:
        vram_mb = probe_gpu_vram_mb(name)
    except Exception:
        vram_mb = None

    return [GPUInfo(
        name=name,
        memory=vram_mb or 0,
        driver_version=rt.gpu_driver or "Unknown",
        manufacturer=manufacturer,
        api_support=[rt.gpu_api] if rt.gpu_api else [],
    )]


def get_memory_info() -> MemoryInfo:
    """获取内存信息。"""
    try:
        mem = psutil.virtual_memory()
        return MemoryInfo(
            total=mem.total,
            available=mem.available,
            used=mem.used,
            percentage=mem.percent
        )
    except Exception:
        return MemoryInfo(0, 0, 0, 0.0)


def get_disk_info() -> List[DiskInfo]:
    """获取磁盘信息。"""
    disks = []

    try:
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                disks.append(DiskInfo(
                    total=usage.total,
                    free=usage.free,
                    used=usage.used,
                    percentage=(usage.used / usage.total) * 100,
                    type=partition.fstype
                ))
            except PermissionError:
                continue
    except Exception:
        # 回退方案
        disks.append(DiskInfo(0, 0, 0, 0.0, "Unknown"))

    return disks


def get_os_info() -> OSInfo:
    """获取操作系统信息。

    Windows 11 与 Windows 10 同属 6.3/10.0 内核，注册表里的 ProductName 在
    Win11 上往往仍写着 "Windows 10"，CurrentVersion 恒为 6.3 的遗留值。
    这里以 ``CurrentBuild`` 为权威：build ≥ 22000 即 Windows 11，
    DisplayVersion（如 23H2）作为 edition。
    """
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            ) as key:
                current_build = str(winreg.QueryValueEx(key, "CurrentBuild")[0])
                product_name = winreg.QueryValueEx(key, "ProductName")[0]
                try:
                    display_version = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
                except OSError:
                    display_version = ""

            try:
                build_no = int(current_build[:5])
            except ValueError:
                build_no = 0
            if build_no >= 22000:
                # Win11 的 ProductName 常遗留为 "Windows 10 ..."，就地纠正
                name = product_name.replace("Windows 10", "Windows 11") \
                    if product_name else "Windows 11"
            elif build_no >= 10240:
                name = "Windows 10"
            else:
                name = product_name or "Windows"

            return OSInfo(
                name=name,
                version=f"10.0.{current_build}",
                build=current_build,
                architecture=platform.machine(),
                edition=display_version,
            )
        except Exception:
            pass

    return OSInfo(
        name=platform.system(),
        version=platform.version(),
        build="",
        architecture=platform.machine(),
        edition="",
    )


def get_directx_info() -> Dict[str, Any]:
    """获取 DirectX 信息（委托指纹层按 System32 特征 DLL 判定的真实版本）。

    WMI 下根本不存在 ``Win32_DirectX`` 类，旧实现必然走 except 返回 Unknown。
    """
    from ..context_builder import cached_runtime

    try:
        rt = cached_runtime()
        version = rt.directx_version
        legacy = rt.directx_legacy
    except Exception:
        version, legacy = None, None

    return {
        "version": version or "Unknown",
        "installed_features": [f"DirectX {version}"] if version else [],
        # True=DX9 旧版托管运行库齐备 / False=缺失 / None=未探测
        "legacy_runtime_complete": legacy,
    }


def get_net_framework_info() -> Dict[str, str]:
    """获取 .NET Framework 信息。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\NET Framework Setup\NDP") as key:
            # 查找最新的 .NET Framework 版本
            versions = []
            for i in range(0, winreg.QueryInfoKey(key)[0]):
                subkey_name = winreg.EnumKey(key, i)
                if subkey_name.startswith("v"):
                    versions.append(subkey_name)

            latest_version = max(versions, default="Unknown")

            # 获取具体版本号
            with winreg.OpenKey(key, latest_version) as subkey:
                try:
                    version = winreg.QueryValueEx(subkey, "Version")[0]
                except:
                    version = "Unknown"

            return {
                "version": version,
                "latest": latest_version
            }
    except Exception:
        return {
            "version": "Unknown",
            "latest": "Unknown"
        }


def get_system_info() -> Dict[str, Any]:
    """获取完整的系统信息。"""
    return {
        "cpu": {
            "name": get_cpu_info().name,
            "cores": get_cpu_info().cores,
            "threads": get_cpu_info().threads,
            "frequency": get_cpu_info().frequency,
            "architecture": get_cpu_info().architecture
        },
        "gpu": [
            {
                "name": gpu.name,
                "memory": gpu.memory,
                "driver_version": gpu.driver_version,
                "manufacturer": gpu.manufacturer
            }
            for gpu in get_gpu_info()
        ],
        "memory": {
            "total": get_memory_info().total,
            "available": get_memory_info().available,
            "used": get_memory_info().used,
            "percentage": get_memory_info().percentage
        },
        "disks": [
            {
                "total": disk.total,
                "free": disk.free,
                "used": disk.used,
                "percentage": disk.percentage,
                "type": disk.type
            }
            for disk in get_disk_info()
        ],
        "os": {
            "name": get_os_info().name,
            "version": get_os_info().version,
            "build": get_os_info().build,
            "architecture": get_os_info().architecture,
            "edition": get_os_info().edition
        },
        "directx": get_directx_info(),
        "net_framework": get_net_framework_info()
    }