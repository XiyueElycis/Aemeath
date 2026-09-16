"""运行时 / 系统环境探测（PRD §4.1 运行时探测）。

探测 DX 版本、VC++ Redistributable、.NET、Java、Wine/Proton、显卡驱动与 API。
系统命令 + 注册表 + psutil。当前为接口 + 骨架，具体探测逻辑留 TODO。
"""

from __future__ import annotations

import platform
import sys
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


class SystemRuntimeProbe:
    """基于 ``platform`` + ``psutil`` 的默认实现骨架。

    目前仅采集操作系统与 CPU 架构；其余运行时组件（图形 API、VC 运行库等）留 TODO。
    """

    def probe(self) -> RuntimeFingerprint:
        # 先用标准库 platform 拿到最基本的系统名称/版本与机器架构
        fp = RuntimeFingerprint(
            os=f"{platform.system()} {platform.release()}",
            arch=platform.machine(),
        )
        # TODO: 探测 directx_version / vc_redist / dotnet / java / wine_proton
        # TODO: 探测 gpu / gpu_driver / gpu_api（Windows 用 dxdiag/wmic，Linux 用 lspci）
        return fp


# 便捷函数
def probe_runtime() -> RuntimeFingerprint:
    """便捷函数：用系统默认探针执行一次运行时探测。"""
    return SystemRuntimeProbe().probe()
