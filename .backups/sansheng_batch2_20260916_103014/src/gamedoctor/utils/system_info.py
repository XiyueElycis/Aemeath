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
    """获取 GPU 信息。"""
    gpus = []

    try:
        c = wmi.WMI()
        for gpu in c.Win32_VideoController():
            # 尝试获取内存信息
            memory = 0
            driver_version = gpu.DriverVersion
            manufacturer = gpu.Manufacturer

            # 获取支持的 API
            api_support = []
            if "DirectX" in gpu.Name:
                api_support.append("DirectX")
            if "OpenGL" in gpu.Name or gpu.Description.lower().contains("opengl"):
                api_support.append("OpenGL")

            gpus.append(GPUInfo(
                name=gpu.Name,
                memory=memory,
                driver_version=driver_version,
                manufacturer=manufacturer,
                api_support=api_support
            ))
    except Exception:
        # 回退方案
        gpu_name = "Unknown GPU"
        try:
            # 尝试从环境变量获取
            gpu_name = os.environ.get("GPU_NAME", gpu_name)
        except:
            pass

        gpus.append(GPUInfo(
            name=gpu_name,
            memory=0,
            driver_version="Unknown",
            manufacturer="Unknown",
            api_support=[]
        ))

    return gpus


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
    """获取操作系统信息。"""
    try:
        import winreg

        # 获取 Windows 版本信息
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            release_id = winreg.QueryValueEx(key, "ReleaseId")[0]
            current_build = winreg.QueryValueEx(key, "CurrentBuild")[0]
            current_version = winreg.QueryValueEx(key, "CurrentVersion")[0]

        # 获取 Windows 版本名称
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
                display_name = winreg.QueryValueEx(key, "ProductName")[0]
        except:
            display_name = "Windows"

        return OSInfo(
            name=display_name,
            version=current_version,
            build=current_build,
            architecture=platform.machine(),
            edition=release_id
        )
    except Exception:
        return OSInfo(
            name=platform.system(),
            version=platform.version(),
            build="",
            architecture=platform.machine(),
            edition=""
        )


def get_directx_info() -> Dict[str, Any]:
    """获取 DirectX 信息。"""
    try:
        c = wmi.WMI()
        dx = c.Win32_DirectX()
        return {
            "version": dx[0].Version if dx else "Unknown",
            "installed_features": [f.Name for f in dx]
        }
    except Exception:
        return {
            "version": "Unknown",
            "installed_features": []
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


def get_system_requirements_check(system_info: Dict[str, Any], requirements: Dict[str, Any]) -> Dict[str, bool]:
    """检查系统是否满足要求。"""
    checks = {}

    # CPU 检查
    if "cpu" in requirements:
        cpu_req = requirements["cpu"]
        cpu_info = system_info["cpu"]
        checks["cpu"] = self._is_cpu_compatible(cpu_info, cpu_req)

    # GPU 检查
    if "gpu" in requirements:
        gpu_req = requirements["gpu"]
        gpu_info = system_info["gpu"][0] if system_info["gpu"] else {}
        checks["gpu"] = self._is_gpu_compatible(gpu_info, gpu_req)

    # 内存检查
    if "memory" in requirements:
        mem_req = requirements["memory"]
        mem_info = system_info["memory"]
        checks["memory"] = mem_info["total"] >= mem_req

    # DirectX 检查
    if "directx" in requirements:
        dx_req = requirements["directx"]
        dx_info = system_info["directx"]
        checks["directx"] = self._is_directx_compatible(dx_info["version"], dx_req)

    return checks


def _is_cpu_compatible(current: Dict[str, Any], required: str) -> bool:
    """检查 CPU 兼容性。"""
    # 简化实现，实际应该解析 CPU 型号和性能指标
    return True


def _is_gpu_compatible(current: Dict[str, Any], required: str) -> bool:
    """检查 GPU 兼容性。"""
    # 简化实现
    return True


def _is_directx_compatible(current: str, required: str) -> bool:
    """检查 DirectX 兼容性。"""
    if not current or not required:
        return True

    try:
        current_ver = float(current.split(" ")[0])
        required_ver = float(required)
        return current_ver >= required_ver
    except:
        return True