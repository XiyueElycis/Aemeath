"""环境指纹（PRD §4.2 性能问题 / 兼容性）。

后台占用、磁盘空间、权限、翻译层等环境因素。当前为接口 + 骨架。
"""

from __future__ import annotations

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


class DefaultEnvironmentScanner:
    """默认实现骨架（可用 psutil）。

    目前仅返回空字典占位；计划用 psutil 采集磁盘剩余空间、内存占用、
    高占用后台进程，以及文件系统大小写敏感度等环境因素。
    """

    def scan(self) -> dict[str, object]:
        # 结果容器：先占位为空，待接入 psutil 后按指标填充键值对
        info: dict[str, object] = {}
        # TODO: 磁盘剩余空间、内存占用、高占用后台进程、文件系统大小写敏感度
        return info


def scan_environment() -> dict[str, object]:
    """便捷函数：用默认扫描器执行一次环境扫描。"""
    return DefaultEnvironmentScanner().scan()
