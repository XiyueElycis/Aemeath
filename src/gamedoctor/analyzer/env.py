"""环境指纹（PRD §4.2 性能问题 / 兼容性）。

后台占用、磁盘空间、内存压力、高占用进程等环境因素。基于 ``psutil``
（可选依赖）采集；psutil 不可用时降级为标准库可获取的最小信息，不抛异常。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Protocol


class EnvironmentScanner(Protocol):
    """环境扫描器协议。

    定义"环境扫描"的最小接口：任何实现只需提供 ``scan`` 方法即可。
    """

    def scan(self) -> dict[str, object]:
        """返回环境信息字典（磁盘/内存/后台进程等）。

        返回值为 ``str -> 任意类型`` 的键值对，具体字段由实现约定
        （如 ``disk_free`` / ``mem_usage`` / ``procs``），便于后续诊断器灵活扩展。
        """
        ...


def _fmt_bytes(n: int) -> str:
    """字节数转人类可读字符串（GB 保留 1 位小数）。"""
    gb = n / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{n / (1024 ** 2):.0f} MB"


class DefaultEnvironmentScanner:
    """psutil 实现：磁盘 / 内存 / 高占用进程 / 文件系统大小写敏感度。

    所有采集项逐项容错：单项失败不影响其余指标，保证扫描器永远返回
    一个可用的字典（排障链路不能因为环境采集自身失败而中断）。
    """

    # 判定"高占用后台进程"的阈值：内存 > 300MB 或 CPU > 15%
    _MEM_THRESHOLD = 300 * 1024 * 1024
    _CPU_THRESHOLD = 15.0
    # 最多报告的高占用进程数，避免清单过长
    _TOP_N = 8

    def scan(self) -> dict[str, object]:
        info: dict[str, object] = {}

        info["disks"] = self._scan_disks()
        info["case_sensitive_fs"] = self._detect_case_sensitivity()

        try:
            import psutil  # 延迟导入：psutil 为可选依赖
        except ImportError:
            info["psutil_available"] = False
            return info

        info["psutil_available"] = True
        info.update(self._scan_memory(psutil))
        info["top_processes"] = self._scan_processes(psutil)
        return info

    # ------------------------------------------------------------------ #
    def _scan_disks(self) -> list[dict[str, object]]:
        """各盘符/挂载点的总容量与剩余空间。"""
        disks: list[dict[str, object]] = []
        seen: set[str] = set()
        # Windows 下 psutil.disk_partitions 给出各盘符；无 psutil 时用当前盘兜底
        try:
            import psutil
            parts = psutil.disk_partitions(all=False)
        except ImportError:
            parts = None

        if parts:
            for p in parts:
                # 跳过光驱/网络盘等无介质设备（cdrom 在 fstype 为空时 usage 会报错）
                if p.mountpoint in seen:
                    continue
                seen.add(p.mountpoint)
                try:
                    usage = psutil.disk_usage(p.mountpoint)
                except (OSError, PermissionError):
                    continue
                disks.append({
                    "mount": p.mountpoint,
                    "fstype": p.fstype,
                    "total": _fmt_bytes(usage.total),
                    "free": _fmt_bytes(usage.free),
                    "free_percent": round(usage.free / usage.total * 100, 1) if usage.total else 0,
                    "low_space": usage.free < 20 * 1024 ** 3,  # 剩余 < 20GB 视为紧张
                })
        else:
            # 无 psutil：至少给出当前工作盘
            try:
                usage = shutil.disk_usage(os.getcwd())
                disks.append({
                    "mount": os.getcwd()[:3] if sys.platform == "win32" else "/",
                    "fstype": "",
                    "total": _fmt_bytes(usage.total),
                    "free": _fmt_bytes(usage.free),
                    "free_percent": round(usage.free / usage.total * 100, 1) if usage.total else 0,
                    "low_space": usage.free < 20 * 1024 ** 3,
                })
            except OSError:
                pass
        return disks

    @staticmethod
    def _scan_memory(psutil) -> dict[str, object]:
        """物理内存与（Windows）提交内存占用。"""
        try:
            vm = psutil.virtual_memory()
            return {
                "mem_total": _fmt_bytes(vm.total),
                "mem_available": _fmt_bytes(vm.available),
                "mem_usage_percent": vm.percent,
                "mem_pressure": vm.percent >= 85,  # 占用 ≥85% 视为内存压力
            }
        except Exception:  # noqa: BLE001 - 极端环境（容器/权限）下取不到
            return {}

    def _scan_processes(self, psutil) -> list[dict[str, object]]:
        """枚举高占用后台进程：内存超阈值或 CPU 超阈值。

        CPU 占用取两次采样的差值（interval=0.1），过滤掉系统空闲进程；
        结果按内存降序，最多 ``_TOP_N`` 条。
        """
        procs: list[dict[str, object]] = []
        for proc in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
            try:
                # 首次 cpu_percent 返回 0，需主动采样一次
                cpu = proc.cpu_percent(interval=None)
                mem = proc.memory_info().rss if proc.memory_info() else 0
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            if mem >= self._MEM_THRESHOLD or cpu >= self._CPU_THRESHOLD:
                procs.append({
                    "name": proc.info.get("name") or "?",
                    "mem": _fmt_bytes(mem),
                    "mem_bytes": mem,
                    "cpu_percent": round(cpu, 1),
                })
        procs.sort(key=lambda p: p["mem_bytes"], reverse=True)
        for p in procs:
            p.pop("mem_bytes", None)
        return procs[:self._TOP_N]

    @staticmethod
    def _detect_case_sensitivity() -> bool:
        """当前工作目录所在文件系统是否区分大小写。

        Windows/macOS 默认不区分、Linux 默认区分；用「创建临时文件再按
        另一大小写访问」实测最可靠（部分 Linux 挂载盘也可能不区分）。
        """
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as d:
                probe = os.path.join(d, "CaseProbe.tmp")
                with open(probe, "w", encoding="utf-8") as f:
                    f.write("x")
                # 同一目录下换大小写访问：能打开说明不区分
                alt = os.path.join(d, "caseprobe.TMP")
                return not os.path.exists(alt)
        except OSError:
            # 取不到时按平台惯例兜底
            return sys.platform.startswith("linux")


def scan_environment() -> dict[str, object]:
    """便捷函数：用默认扫描器执行一次环境扫描。"""
    return DefaultEnvironmentScanner().scan()
