"""运行时级修复原语（PRD §4.5.1 运行时级）。

安装 / 修复 VC++、.NET、DirectX 运行库。这类动作有两个不可逾越的边界：

1. **系统级安装无法用游戏目录备份回滚**——安装器改的是 WinSxS / 注册表 /
   系统目录，不满足「凡备份必可还原」；
2. **安装器必须 UAC 提权**，"静默"只是加了 ``/quiet`` 参数，授权动作依然
   发生在用户桌面上。

因此 :class:`InstallRuntimePrimitive` 固定 **L3 永不自动执行**：

- 检测是真实的（注册表 / ``dotnet --list-runtimes`` / 系统目录特征 DLL，
  见 :mod:`gamedoctor.fingerprint.runtime`），指引里直接告诉用户"装没装"；
- 安装只给**微软官方直链 + 安装包文件名 + 静默参数 + 手动步骤**，由用户
  自行下载执行，本工具绝不代下载、代运行系统安装器。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ...models import (
    ActionLevel,
    ActionResult,
    FixStatus,
    RuntimeFingerprint,
    TechStackFingerprint,
)
from .base import RepairPrimitive, register

_VC_QUIET = "/install /quiet /norestart"


@dataclass(frozen=True)
class RuntimeComponent:
    """一个可指引安装的运行库组件（全部为微软官方来源）。"""

    id: str            # 规范化组件 id，与 RuntimeFingerprint.vc_components 对齐
    display: str       # 面向用户的中文名
    download_url: str  # 微软官方直链或官方下载页
    installer: str     # 保存到本地后的安装包文件名
    quiet_args: str    # 静默安装参数（需管理员权限）；无则为空串
    notes: str = ""    # 额外手动步骤/说明


# 运行库目录：链接均为微软官方永久直链（aka.ms / download.microsoft.com /
# go.microsoft.com / 官方下载详情页），新增条目只允许使用微软官方域名。
_CATALOG: dict[str, RuntimeComponent] = {
    # ---- Visual C++ Redistributable（游戏缺 DLL 的头号原因）---- #
    "vc2015-2022_x64": RuntimeComponent(
        id="vc2015-2022_x64",
        display="Microsoft Visual C++ 2015-2022 Redistributable (x64)",
        download_url="https://aka.ms/vs/17/release/vc_redist.x64.exe",
        installer="vc_redist.x64.exe", quiet_args=_VC_QUIET,
    ),
    "vc2015-2022_x86": RuntimeComponent(
        id="vc2015-2022_x86",
        display="Microsoft Visual C++ 2015-2022 Redistributable (x86)",
        download_url="https://aka.ms/vs/17/release/vc_redist.x86.exe",
        installer="vc_redist.x86.exe", quiet_args=_VC_QUIET,
        notes="64 位系统也建议同时安装 x86 版——32 位游戏只找 32 位运行库。",
    ),
    "vc2013_x64": RuntimeComponent(
        id="vc2013_x64",
        display="Microsoft Visual C++ 2013 Redistributable (x64)",
        download_url="https://aka.ms/highdpimfc2013x64enu",
        installer="vcredist_2013_x64.exe", quiet_args=_VC_QUIET,
    ),
    "vc2013_x86": RuntimeComponent(
        id="vc2013_x86",
        display="Microsoft Visual C++ 2013 Redistributable (x86)",
        download_url="https://aka.ms/highdpimfc2013x86enu",
        installer="vcredist_2013_x86.exe", quiet_args=_VC_QUIET,
    ),
    "vc2012_x64": RuntimeComponent(
        id="vc2012_x64",
        display="Microsoft Visual C++ 2012 Update 4 Redistributable (x64)",
        download_url="https://download.microsoft.com/download/1/6/B/"
                     "16B06F60-3B20-4FF2-B699-5E9B7962F9AE/VSU_4/vcredist_x64.exe",
        installer="vcredist_2012_x64.exe", quiet_args=_VC_QUIET,
    ),
    "vc2012_x86": RuntimeComponent(
        id="vc2012_x86",
        display="Microsoft Visual C++ 2012 Update 4 Redistributable (x86)",
        download_url="https://download.microsoft.com/download/1/6/B/"
                     "16B06F60-3B20-4FF2-B699-5E9B7962F9AE/VSU_4/vcredist_x86.exe",
        installer="vcredist_2012_x86.exe", quiet_args=_VC_QUIET,
    ),
    "vc2010_x64": RuntimeComponent(
        id="vc2010_x64",
        display="Microsoft Visual C++ 2010 SP1 Redistributable (x64)",
        download_url="https://download.microsoft.com/download/1/6/5/"
                     "165255E7-1014-4D0A-B094-B6A430A6BFFC/vcredist_x64.exe",
        installer="vcredist_2010_x64.exe", quiet_args="/q /norestart",
    ),
    "vc2010_x86": RuntimeComponent(
        id="vc2010_x86",
        display="Microsoft Visual C++ 2010 SP1 Redistributable (x86)",
        download_url="https://download.microsoft.com/download/1/6/5/"
                     "165255E7-1014-4D0A-B094-B6A430A6BFFC/vcredist_x86.exe",
        installer="vcredist_2010_x86.exe", quiet_args="/q /norestart",
    ),
    # ---- .NET ---- #
    "dotnetfx48": RuntimeComponent(
        id="dotnetfx48",
        display=".NET Framework 4.8（离线安装包）",
        download_url="https://go.microsoft.com/fwlink/?linkid=2088631",
        installer="ndp48-x86-x64-allos-enu.exe", quiet_args="/q /norestart",
        notes="Win10 1903+ / Win11 系统已内置 4.8，仅在探测明确缺失时安装。",
    ),
    "dotnet_desktop_8_x64": RuntimeComponent(
        id="dotnet_desktop_8_x64",
        display=".NET 8.0 Desktop Runtime (x64)",
        download_url="https://aka.ms/dotnet/8.0/windowsdesktop-runtime-win-x64.exe",
        installer="windowsdesktop-runtime-8.0-win-x64.exe",
        quiet_args=_VC_QUIET,
        notes="游戏/工具报 'hostfxr' / 'WindowsDesktop.App' 缺失时装这个，"
              "不要装成 ASP.NET Core Runtime。",
    ),
    "dotnet_desktop_9_x64": RuntimeComponent(
        id="dotnet_desktop_9_x64",
        display=".NET 9.0 Desktop Runtime (x64)",
        download_url="https://aka.ms/dotnet/9.0/windowsdesktop-runtime-win-x64.exe",
        installer="windowsdesktop-runtime-9.0-win-x64.exe",
        quiet_args=_VC_QUIET,
    ),
    # ---- DirectX ---- #
    "directx_june2010": RuntimeComponent(
        id="directx_june2010",
        display="DirectX End-User Runtimes (June 2010)",
        download_url="https://www.microsoft.com/download/details.aspx?id=8109",
        installer="directx_Jun2010_redist.exe", quiet_args="DXSETUP.exe /silent",
        notes="专治 d3dx9_43.dll / x3daudio1_7.dll / xinput1_3.dll 缺失。"
              "先运行 redist 包解压到任意空目录，再以管理员身份在解压目录"
              "执行 DXSETUP.exe /silent；不影响系统自带的 DX11/12。",
    ),
}

# 目录的公开只读视图（测试与提示词可枚举）
SUPPORTED_RUNTIME_COMPONENTS = dict(_CATALOG)

_STATE_TEXT = {True: "已安装", False: "未安装", None: "未知"}

_VC_FAMILIES = (
    # v14/14.x 工具集即 VS2015~2022 同代运行库（部分注册名只写 "Visual C++ v14"）
    (r"2015\s*-\s*2022|2022|2019|2017|2015|v14|14\.\d+", "vc2015-2022"),
    (r"2013", "vc2013"),
    (r"2012", "vc2012"),
    (r"2010", "vc2010"),
)


def component_installed(
    component_id: str, rt: RuntimeFingerprint,
) -> tuple[bool | None, str]:
    """依据运行时指纹判定组件安装状态，返回 ``(是否安装, 证据文案)``。

    无法判定（非 Windows / 未探测）时第一元素为 None。纯函数，便于单测。
    """
    if component_id.startswith("vc"):
        present = component_id in rt.vc_components
        return (
            present,
            "注册表卸载项已包含该运行库" if present else "注册表卸载项中未见该运行库",
        )
    if component_id == "dotnetfx48":
        if not rt.dotnet:
            return None, "未探测到 .NET Framework 版本"
        m = re.match(r"(\d+)\.(\d+)", rt.dotnet)
        ok = bool(m and (int(m.group(1)), int(m.group(2))) >= (4, 8))
        return ok, f"当前 .NET Framework {rt.dotnet}"
    if component_id == "dotnet_desktop_8_x64":
        hit = [v for v in rt.dotnet_runtimes if v.startswith("WindowsDesktop 8.")]
        return (bool(hit), f"已安装 {hit[0]}" if hit else "未见 WindowsDesktop 8.x 运行时")
    if component_id == "dotnet_desktop_9_x64":
        hit = [v for v in rt.dotnet_runtimes if v.startswith("WindowsDesktop 9.")]
        return (bool(hit), f"已安装 {hit[0]}" if hit else "未见 WindowsDesktop 9.x 运行时")
    if component_id == "directx_june2010":
        if rt.directx_legacy is None:
            return None, "未探测（可能非 Windows 环境）"
        return (
            rt.directx_legacy,
            "系统目录已含 d3dx9_43.dll" if rt.directx_legacy
            else "系统目录缺少 d3dx9_43.dll（DX9 旧扩展未装）",
        )
    return None, "未知组件"


def resolve_component(raw: str) -> str | None:
    """把 LLM/用户给的 component 文案归一化为目录 id；无法识别返回 None。

    接受精确 id（``vc2015-2022_x64``）与自然说法（``"VC++ 2015-2022 x64"``、
    ``"vcredist 2013 64位"``、``".NET Framework 4.8"``、``"directx 9"``、
    ``"缺 d3dx9_43.dll"``）。
    """
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text in _CATALOG:
        return text

    # 架构线索（无明确架构时：新运行库默认 x64，老运行库默认 x86）
    if "arm64" in text:
        arch = "arm64"
    elif "x86" in text or "32" in text:
        arch = "x86"
    elif "x64" in text or "64" in text:
        arch = "x64"
    else:
        arch = ""

    is_vc = (
        "visual c" in text or "vc++" in text or "vcredist" in text
        or "vc_redist" in text or text.startswith("vc")
    )
    if is_vc:
        family = next(
            (fid for pat, fid in _VC_FAMILIES if re.search(pat, text)), None,
        )
        if family is None:
            # 只说"缺 VC++ 运行库"未给年份 → 给现役统一版本
            family = "vc2015-2022"
        use_arch = arch or ("x64" if family == "vc2015-2022" else "x86")
        candidate = f"{family}_{use_arch}"
        return candidate if candidate in _CATALOG else None

    # DirectX 旧运行库（必须在 .NET 判定之前：d3dx/xinput 关键词不含歧义）
    if any(k in text for k in ("directx", "d3dx", "dx9", "x3daudio", "xinput")):
        return "directx_june2010"

    if ".net" in text or "dotnet" in text:
        if any(k in text for k in ("framework", "4.8", "4.7", "net4")):
            return "dotnetfx48"
        if "9" in text and ("desktop" in text or "runtime" in text or re.search(r"\b9\b", text)):
            return "dotnet_desktop_9_x64"
        if "8" in text and ("desktop" in text or "runtime" in text or re.search(r"\b8\b", text)):
            return "dotnet_desktop_8_x64"
        if "desktop" in text or "core" in text:
            return "dotnet_desktop_8_x64"
        # 仅说"缺 .net"：老游戏最常见的是 Framework 4.8
        return "dotnetfx48"
    return None


def build_guidance(component_id: str, fp: TechStackFingerprint) -> str:
    """生成 L3 人工指引全文（纯只读，可在 dry-run 中调用）。"""
    comp = _CATALOG.get(component_id)
    if comp is None:
        ids = "、".join(sorted(_CATALOG))
        return f"【未知运行库组件】支持的 component 取值：{ids}"

    installed, evidence = component_installed(component_id, fp.runtime)
    lines = [
        f"【组件】{comp.display}",
        f"【当前状态】{_STATE_TEXT[installed]}（{evidence}）",
        f"【官方下载】{comp.download_url}",
        f"【安装包】{comp.installer}（运行需管理员 / UAC 提权）",
    ]
    if comp.quiet_args:
        lines.append(f"【静默参数】{comp.installer} {comp.quiet_args}")
    if comp.notes:
        lines.append(f"【说明】{comp.notes}")
    lines.append(
        "【安全提示】系统级安装不可由本工具备份回滚；请只从上述微软官方"
        "域名下载，安装完成后重新运行诊断/游戏验证问题是否消失。"
    )
    return "\n".join(lines)


@register
class InstallRuntimePrimitive(RepairPrimitive):
    """安装/修复系统运行库（VC++ / .NET / DirectX）——固定 L3 仅指引。"""

    name = "install_runtime"
    category = "runtime"
    default_level = ActionLevel.L3_FORBIDDEN
    summary = (
        "安装/修复 VC++/.NET/DirectX 运行库（L3 仅指引：给出当前检测状态、"
        "微软官方直链与静默安装参数，永不自动下载或执行系统安装器）"
    )

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        return []  # 指引型动作：不动游戏文件，也没有可备份的对象

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        # L3 在执行器层已被短路拦截，永不调用本方法；防御性实现仍返回指引，
        # 避免直接调用原语时退回"未实现"。
        return ActionResult(
            status=FixStatus.NEEDS_MANUAL,
            message=build_guidance(resolve_component(str(params.get("component", ""))), fp),
        )

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        component_id = resolve_component(str(params.get("component", "")))
        if component_id is None:
            return False
        installed, _ = component_installed(component_id, fp.runtime)
        return bool(installed)

    def preview(self, params: dict, fp: TechStackFingerprint) -> str:
        return self.guidance(params, fp)

    def guidance(self, params: dict, fp: TechStackFingerprint) -> str:
        component_id = resolve_component(str(params.get("component", "")))
        if component_id is None:
            return build_guidance("", fp)
        return build_guidance(component_id, fp)
