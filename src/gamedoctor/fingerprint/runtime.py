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
        # 原始显示名 → 规范化组件 id（vc2015-2022_x64 等），供修复原语判定缺什么
        fp.vc_components = sorted({
            c["id"] for name in fp.vc_redist
            if (c := parse_vc_redist_name(name))
        })
        fp.dotnet = self._probe_dotnet()
        fp.dotnet_runtimes = self._probe_dotnet_runtimes()
        fp.directx_legacy = self._probe_directx_legacy()
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

    def _probe_dotnet_runtimes(self) -> list[str]:
        """枚举 .NET(Core) 已安装运行时（``dotnet --list-runtimes``）。

        只保留游戏桌面应用会依赖的两类：WindowsDesktop 与 NETCore，
        输出形如 ``"WindowsDesktop 8.0.4"``；未装 dotnet/命令失败 → 空列表。
        """
        out = _run_cmd(["dotnet", "--list-runtimes"])
        if not out:
            return []
        found: list[str] = []
        for line in out.splitlines():
            item = parse_dotnet_runtime_line(line)
            if item and item not in found:
                found.append(item)
        return found

    def _probe_directx_legacy(self) -> Optional[bool]:
        """检测 DirectX 9 旧版托管扩展（June 2010 运行库）是否齐备。

        现代 Windows 自带 DX11/12 运行时，但老游戏依赖的 d3dx9_43 /
        x3daudio1_7 / xinput1_3 等**不在系统内建范围**，要装 DirectX
        End-User Runtimes (June 2010)。以最常报缺的 ``d3dx9_43.dll``
        作为代理标记：64 位系统要求 System32 与 SysWOW64（32 位游戏）
        两处都在；非 Windows 或目录异常返回 None。
        """
        win = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        system32 = win / "System32"
        if not system32.exists():
            return None
        dirs = [system32]
        syswow64 = win / "SysWOW64"
        if syswow64.exists():
            dirs.append(syswow64)
        return all((d / "d3dx9_43.dll").exists() for d in dirs)

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


# VC++ 注册表显示名 → 家族 id（顺序敏感：2015-2022 必须先于单年份匹配）
# 注意：14.x 工具集（VS2015~2022 同代）在部分机器上注册名是 "Visual C++ v14
# Redistributable"，不带年份，需用 v14/14.x 版本号兜底。
_VC_YEAR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"2015\s*-\s*2022|2022|2019|2017|2015|v14|14\.\d+", "vc2015-2022"),
    (r"2013", "vc2013"),
    (r"2012", "vc2012"),
    (r"2010", "vc2010"),
    (r"2008", "vc2008"),
    (r"2005", "vc2005"),
)


def parse_vc_redist_name(name: str) -> Optional[dict]:
    """把注册表卸载项的 VC++ 显示名解析为结构化组件。

    示例：
    ``"Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.44.35207"``
    → ``{"family": "vc2015-2022", "arch": "x64", "version": "14.44.35207",
    "id": "vc2015-2022_x64"}``

    2013 的 "Minimum Runtime" / "Additional Runtime" 成对出现，解析出同一
    组件 id，由调用方去重。非 VC++ 名称返回 None。
    """
    if "Visual C++" not in name:
        return None
    family = None
    for pattern, family_id in _VC_YEAR_PATTERNS:
        if re.search(pattern, name):
            family = family_id
            break
    if family is None:
        return None

    low = name.lower()
    if "arm64" in low:
        arch = "arm64"
    elif "x64" in low or "64-bit" in low:
        arch = "x64"
    elif "x86" in low or "32-bit" in low:
        arch = "x86"
    else:
        # 2005/2008/2010 早期安装包名称不带架构标注，均为 32 位
        arch = "x86"

    # 版本号位置不固定：新版在 "- 14.44.35207"，2008 版在 "- x64 9.0.30729.6161"。
    # 取名称中最后一个点分版本号（年份无点号，不会被误匹配）。
    dotted = re.findall(r"\d+\.\d+(?:\.\d+)*", name)
    version = dotted[-1] if dotted else None
    return {"family": family, "arch": arch, "version": version,
            "id": f"{family}_{arch}"}


def parse_dotnet_runtime_line(line: str) -> Optional[str]:
    """解析 ``dotnet --list-runtimes`` 单行，返回 ``"WindowsDesktop 8.0.4"``。

    只认 WindowsDesktop（桌面游戏/工具依赖）与 NETCore；ASP.NET Core 与
    格式异常的行返回 None。
    """
    m = re.match(
        r"Microsoft\.(WindowsDesktop|NETCore)\.App\s+(\d+\.\d+\.\d+)",
        line.strip(),
    )
    if not m:
        return None
    kind = "WindowsDesktop" if m.group(1) == "WindowsDesktop" else "NETCore"
    return f"{kind} {m.group(2)}"


# 便捷函数
def probe_runtime() -> RuntimeFingerprint:
    """便捷函数：用系统默认探针执行一次运行时探测。"""
    return SystemRuntimeProbe().probe()
