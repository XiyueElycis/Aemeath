"""运行时 / 系统环境探测（PRD §4.1 运行时探测）。

探测 DX 版本、VC++ Redistributable、.NET、Java、Wine/Proton、显卡与驱动。

Windows 以注册表 + System32 特征 DLL + PowerShell CIM 为主；Linux 走
``lspci`` / ``which``。所有外部命令带超时，任何单项失败都降级为
``None``/空列表，不抛异常——排障链路不能因探测自身失败而中断。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional
from typing import Protocol

from ..models import RuntimeFingerprint


class RuntimeProbe(Protocol):
    """运行时探测协议。

    定义"运行时探测"的最小接口，任何实现只需提供 ``probe`` 方法。
    """

    def probe(self) -> RuntimeFingerprint:
        """返回运行时指纹。

        具体包含 OS/架构/图形 API/运行库等字段，见 ``RuntimeFingerprint`` 定义。
        """
        ...


def _run_cmd(cmd: list[str], timeout: float = 8.0) -> Optional[str]:
    """执行外部命令并返回 stdout；失败/超时返回 None。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


class SystemRuntimeProbe:
    """注册表 + 特征文件 + 系统命令的默认实现。"""

    def probe(self) -> RuntimeFingerprint:
        fp = RuntimeFingerprint(
            os=f"{platform.system()} {platform.release()}",
            arch=platform.machine(),
        )
        if sys.platform == "win32":
            self._probe_windows(fp)
        elif sys.platform.startswith("linux"):
            self._probe_linux(fp)

        fp.java = self._probe_java()
        fp.wine_proton = self._probe_wine()
        return fp

    # ------------------------------------------------------------------ #
    def _probe_windows(self, fp: RuntimeFingerprint) -> None:
        fp.directx_version = self._probe_directx()
        fp.vc_redist = self._probe_vc_redist()
        fp.dotnet = self._probe_dotnet()
        gpu_name, driver = self._probe_gpu_windows()
        fp.gpu = gpu_name
        fp.gpu_driver = driver
        if gpu_name:
            # DX 版本已能说明图形 API；无 DX 信息时按 GPU 厂商粗判
            fp.gpu_api = f"DirectX {fp.directx_version}" if fp.directx_version else None

    def _probe_linux(self, fp: RuntimeFingerprint) -> None:
        gpu_name = self._probe_gpu_linux()
        fp.gpu = gpu_name
        if gpu_name:
            # Linux 游戏走 Proton/Vulkan 为主
            fp.gpu_api = "Vulkan"

    # ------------------------------------------------------------------ #
    def _probe_directx(self) -> Optional[str]:
        """通过 System32 特征 DLL 判断 DirectX 最高版本。

        d3d12core.dll 存在 → DX12 Ultimate；d3d12.dll → DX12；
        仅 d3d11.dll → DX11。
        """
        sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        if (sysdir / "d3d12core.dll").exists():
            return "12 Ultimate"
        if (sysdir / "d3d12.dll").exists():
            return "12"
        if (sysdir / "d3d11.dll").exists():
            return "11"
        return None

    def _probe_vc_redist(self) -> list[str]:
        """从卸载注册表枚举已安装的 Visual C++ Redistributable。"""
        versions: list[str] = []
        try:
            import winreg
        except ImportError:
            return versions
        # 64 位系统上 32 位安装包在 WOW6432Node 下
        subkeys = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for root, subkey in subkeys:
            try:
                with winreg.OpenKey(root, subkey) as parent:
                    i = 0
                    while True:
                        try:
                            child = winreg.EnumKey(parent, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(parent, child) as key:
                                name, _ = winreg.QueryValueEx(key, "DisplayName")
                        except OSError:
                            continue
                        name = str(name)
                        if "Visual C++" in name and "Redistributable" in name:
                            # 规范化为 "VC++ 2015-2022 x64" 这类短名
                            short = re.sub(r"\s+", " ", name).strip()
                            if short not in versions:
                                versions.append(short)
            except OSError:
                continue
        return versions

    def _probe_dotnet(self) -> Optional[str]:
        """读取 .NET Framework 4.x Release 值并映射到版本号。"""
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
            ) as key:
                release, _ = winreg.QueryValueEx(key, "Release")
        except OSError:
            return None
        # Release → 版本映射（微软官方对照表，4.5 起步）
        table = [
            (528040, "4.8"), (461808, "4.7.2"), (461308, "4.7.1"),
            (460798, "4.7"), (394802, "4.6.2"), (394254, "4.6.1"),
            (393295, "4.6"), (379893, "4.5.2"), (378675, "4.5.1"),
            (378389, "4.5"),
        ]
        for threshold, ver in table:
            if release >= threshold:
                return ver
        return f"4.x (release {release})"

    def _probe_gpu_windows(self) -> tuple[Optional[str], Optional[str]]:
        """用 PowerShell CIM 查询显卡名与驱动版本（wmic 在新系统已移除）。

        优先选择物理独显/核显（NVIDIA/AMD/Intel），跳过向日葵/Parsec 等
        虚拟显示适配器（它们常排在 CIM 枚举结果的第一位）。
        """
        out = _run_cmd([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_VideoController | "
            "Select-Object Name,DriverVersion | ConvertTo-Csv -NoTypeInformation)",
        ])
        if not out:
            return None, None
        # CSV 输出：首行表头，其余每行一块显卡，字段带引号
        rows = [ln for ln in out.strip().splitlines() if ln.strip()][1:]
        cards: list[tuple[str, str]] = []
        for row in rows:
            cells = [c.strip('"') for c in row.split('","')]
            cells = [c.strip('"') for c in cells]
            if cells and cells[0]:
                cards.append((cells[0], cells[1] if len(cells) > 1 else ""))
        if not cards:
            return None, None

        virtual_markers = ("oray", "idd", "parsec", "virtual", "remote", "teamviewer",
                           "sunlogin", "splashtop", "hyper-v", "vmware")
        physical_keywords = ("nvidia", "geforce", "rtx", "gtx", "quadro",
                             "amd", "radeon", "intel", "arc", "iris")
        for name, driver in cards:
            low = name.lower()
            if any(k in low for k in physical_keywords) and not any(
                v in low for v in virtual_markers
            ):
                return name, (driver or None)
        # 没有匹配到物理显卡：用第一个非虚拟的兜底
        for name, driver in cards:
            if not any(v in name.lower() for v in virtual_markers):
                return name, (driver or None)
        return cards[0][0], (cards[0][1] or None)

    def _probe_gpu_linux(self) -> Optional[str]:
        """lspci 枚举 VGA/3D 控制器。"""
        out = _run_cmd(["lspci"])
        if not out:
            return None
        for line in out.splitlines():
            low = line.lower()
            if "vga" in low or "3d controller" in low:
                # 去掉 "00:02.0 VGA compatible controller: " 前缀
                return line.split(": ", 1)[-1].strip()
        return None

    def _probe_java(self) -> Optional[str]:
        """java -version 探测（输出在 stderr）。"""
        java = shutil.which("java")
        if not java:
            return None
        try:
            proc = subprocess.run(
                [java, "-version"], capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return "已安装（版本未知）"
        text = proc.stderr or proc.stdout or ""
        m = re.search(r'version "([^"]+)"', text)
        return m.group(1) if m else "已安装（版本未知）"

    def _probe_wine(self) -> Optional[str]:
        """Wine/Proton 环境探测。"""
        # Wine 会设置 WINEPREFIX/WINELOADER；Steam Proton 运行时另有标识
        if os.environ.get("WINEPREFIX") or os.environ.get("WINELOADER"):
            wine = shutil.which("wine")
            return f"Wine（{wine}）" if wine else "Wine"
        if shutil.which("wine"):
            return "wine 可用"
        if os.environ.get("STEAM_COMPAT_DATA_PATH"):
            return "Proton（Steam 运行时）"
        return None


# 便捷函数
def probe_runtime() -> RuntimeFingerprint:
    """便捷函数：用系统默认探针执行一次运行时探测。"""
    return SystemRuntimeProbe().probe()
