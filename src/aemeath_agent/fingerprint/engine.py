"""引擎签名库与识别（PRD §4.1 引擎特征库）。

不预设游戏列表，只做"引擎 + 版本"的命名/哈希匹配。签名库可增量维护：
向 ``ENGINE_SIGNATURES`` 追加条目即可，无需改代码。
"""

from __future__ import annotations

import hashlib
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..models import EngineFingerprint

# 识别扫描范围：根目录往下 3 层（覆盖 Client/Binaries/Win64、Engine/Binaries 等）
_MAX_SCAN_DEPTH = 3
# 目录数护栏：超大安装目录下不无限下钻
_MAX_SCAN_DIRS = 400

# --------------------------------------------------------------------------- #
# 引擎签名库（可增量维护）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EngineSignature:
    """单条引擎签名规则。``markers`` 命中任一即认为匹配。"""

    engine: str
    markers: tuple[str, ...]                     # 文件名/路径片段，如 "UnityPlayer.dll"
    default_version: str | None = None
    config_hint: str = ""                        # 额外说明（如版本文件路径）

    def matches(self, name: str) -> bool:
        """判断文件名 ``name`` 是否命中本签名的任一 marker（大小写不敏感）。"""
        return any(m.lower() in name.lower() for m in self.markers)


ENGINE_SIGNATURES: tuple[EngineSignature, ...] = (
    EngineSignature("unity", ("unityplayer.dll", "unitycrashhandler64.exe", "unityplayer.so"),
                    config_hint="ProjectSettings/ProjectVersion.txt"),
    EngineSignature("unreal", ("unrealed", "unrealengine", "ue4editor", "unrealeditor",
                               ".uproject", "shipping.exe"),
                    config_hint="Engine/Build/Build.version"),
    EngineSignature("godot", ("libgodot.so", "godot.exe", "godot_", ".pck"),
                    config_hint="project.godot"),
    EngineSignature("renpy", ("renpy.exe", "renpy", "lib/renpy"),
                    config_hint="renpy/version.txt"),
    EngineSignature("electron", ("electron.exe", "nw.exe", "resources/app"),
                    default_version=None, config_hint="package.json"),
    EngineSignature("java", (".jar", "jre", "java.exe"), default_version=None),
)


# --------------------------------------------------------------------------- #
# 识别接口
# --------------------------------------------------------------------------- #


class EngineDetector(Protocol):
    """引擎识别器协议。

    定义"引擎识别"的最小接口，任何实现只需提供 ``identify`` 方法即可。
    """

    def identify(self, game_dir: Path) -> EngineFingerprint | None:
        """扫描 ``game_dir``，返回引擎指纹；无法识别返回 ``None``。

        :param game_dir: 游戏安装目录。
        :return: 识别到的 ``EngineFingerprint``；无匹配时返回 ``None``。
        """
        ...


def _md5(path: Path) -> str:
    """分块计算文件 MD5 并返回十六进制摘要（供后续哈希校验使用）。"""
    h = hashlib.md5()
    try:
        with path.open("rb") as f:
            # 流式读取 8KB 分块，避免大文件一次性读入内存
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        # 读取失败返回空串，调用方据此视为"无法校验"
        return ""
    return h.hexdigest()


class SimpleEngineDetector:
    """基于文件命名模式 + 可选哈希的默认实现（哈希校验留 TODO）。

    扫描范围是「顶层 + 最多 :data:`_MAX_SCAN_DEPTH` 层子目录」的文件名：
    UE/Godot 这类引擎的特征文件都在子目录里（如
    ``Client/Binaries/Win64/Client-Win64-Shipping.exe``、``Content/*.pck``），
    只看顶层会整片漏掉，所以按相对路径逐层比对 marker。
    暂不做文件内容级校验。
    """

    def identify(self, game_dir: Path) -> EngineFingerprint | None:
        if not game_dir.is_dir():
            return None
        # 广度优先：同层内按签名表顺序判定，顶层命中天然优先于深层命中。
        # 命中即返回（不做全树扫描），目录数触顶则放弃继续下钻。
        queue: deque[tuple[Path, int]] = deque([(game_dir, 0)])
        visited = 0
        while queue:
            path, depth = queue.popleft()
            if visited >= _MAX_SCAN_DIRS:
                break
            visited += 1
            try:
                with os.scandir(path) as it:
                    entries = list(it)
            except OSError:
                continue          # 权限不足/目录消失：跳过该目录，不阻断识别
            for entry in entries:
                for sig in ENGINE_SIGNATURES:
                    if sig.matches(entry.name):
                        return EngineFingerprint(
                            engine=sig.engine,
                            version=sig.default_version,
                            confidence=0.8,  # 命名匹配的置信度留有余量，哈希校验可提升
                        )
            if depth >= _MAX_SCAN_DEPTH:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        queue.append((Path(entry.path), depth + 1))
                except OSError:
                    continue
        return None


# 便捷函数：后续实现可替换为更复杂的探测器
detect_engine = SimpleEngineDetector().identify
