"""文件扫描（PRD §4.2 资源加载 / 文件完整性）。

扫描缺失资源、冲突 Mod 覆盖、文件校验和。当前为接口 + 骨架。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from ..models import TechStackFingerprint
from ..textfile import read_text_preview


class FileScanner(Protocol):
    """文件扫描器协议。

    定义"文件扫描"的最小接口，任何实现只需提供 ``scan`` 方法即可。
    """

    def scan(self, fp: TechStackFingerprint) -> list[str]:
        """返回发现的问题清单（如 "缺失 x.dll" / "检测到冲突 Mod"）。

        :param fp: 技术栈指纹，提供引擎型号 / 关键文件等信息作为扫描依据。
        :return: 人类可读的问题描述列表，空列表表示未发现问题。
        """
        ...


def _sha256(path: Path) -> str:
    """分块计算文件 SHA-256 并返回十六进制摘要。

    以 8KB 分块流式读取，避免大文件一次性读入内存；
    读取失败（文件不存在/无权限）时返回空串，由调用方判断异常。
    """
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            # iter(..., b"")：反复读取 8KB，直到读到空字节才停止，实现流式哈希
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        # 读取异常视为无法校验，返回空串表示"未获得校验值"
        return ""
    return h.hexdigest()


class DefaultFileScanner:
    """默认实现骨架：占位返回空清单，待补充真实扫描逻辑。"""

    def scan(self, fp: TechStackFingerprint) -> list[str]:
        # 结果容器：为每个发现的问题追加一行人类可读描述
        issues: list[str] = []
        # TODO: 校验关键运行时 DLL 是否存在（依赖 RuntimeFingerprint）
        # TODO: 识别 Mod 目录（由引擎指纹给出路径），检测冲突覆盖
        return issues


def scan_files(fp: TechStackFingerprint) -> list[str]:
    """便捷函数：用默认扫描器扫描指定技术栈指纹对应的文件集合。"""
    return DefaultFileScanner().scan(fp)


def peek_text_file(path: Path, *, max_chars: int = 2000) -> str:
    """读取目标配置/脚本文本的摘要，**只读不改**（信息采集阶段的"读"）。

    它是「文本编辑闭环」的另一半：编辑前先读一遍，LLM 才知道该用哪个**锚点**
    去改；报告里也可以用这段摘要向用户展示"改之前长什么样"。

    :param max_chars: 最多返回多少字符，超出部分截断并标注。
    :return: 带文件元信息（编码 / 行尾 / 是否截断）的文本预览。
    """
    return read_text_preview(path, max_chars=max_chars)
