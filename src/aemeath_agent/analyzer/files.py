"""文件扫描（PRD §4.2 资源加载 / 文件完整性）。

基于技术栈指纹对游戏安装目录做只读体检：

- **Mod 加载器冲突**：BepInEx 与 MelonLoader 同目录共存会互相注入导致
  崩溃（Unity 游戏最常见）；多个 Mod 加载器同名共存一并报告。
- **零字节文件**：下载中断/杀软隔离的典型特征。
- **大小写明冲突**：不区分大小写的文件系统上，同名不同大小写的文件
  会互相覆盖（迁移到 Linux/Proton 时必炸）。
- **引擎关键文件缺失**：Unity 的 ``*_Data/Managed/Assembly-CSharp.dll``
  与 Unreal 的 ``*.pak`` 缺失通常意味着安装不完整。

所有扫描只读，不触碰游戏文件；找不到安装目录时返回空清单。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from ..models import TechStackFingerprint
from ..textfile import read_text_preview

# 已知 Mod 加载器的目录/文件特征（Unity 生态为主）
_MOD_LOADER_MARKERS = {
    "BepInEx": ["BepInEx", "winhttp.dll", "doorstop_config.ini"],
    "MelonLoader": ["MelonLoader", "MelonLoader.dll", "version.dll"],
    "SMAPI": ["SMAPI", "StardewModdingAPI.exe"],
    "Fabric": ["fabric-loader-*.jar", "mods"],
}

# 扫描零字节文件的最大深度与文件数上限，避免对超大安装目录全盘 rglob
_MAX_SCAN_FILES = 20000


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
    """安装目录只读体检：Mod 冲突 / 零字节 / 大小写冲突 / 引擎关键文件。"""

    def scan(self, fp: TechStackFingerprint) -> list[str]:
        issues: list[str] = []

        root: Path | None = fp.platform.install_path if fp.platform else None
        if root is None or not root.exists() or not root.is_dir():
            return issues  # 没有安装目录可扫（未识别平台/未安装）

        issues.extend(self._scan_mod_loaders(root))
        issues.extend(self._scan_engine_keyfiles(root, fp))
        issues.extend(self._scan_file_integrity(root))
        return issues

    # ------------------------------------------------------------------ #
    def _scan_mod_loaders(self, root: Path) -> list[str]:
        """检测 Mod 加载器共存冲突。"""
        issues: list[str] = []
        found: dict[str, list[str]] = {}
        for loader, markers in _MOD_LOADER_MARKERS.items():
            present: list[str] = []
            for marker in markers:
                if "*" in marker:
                    if any(root.glob(marker)):
                        present.append(marker)
                elif (root / marker).exists():
                    present.append(marker)
            if present:
                found[loader] = present

        # BepInEx + MelonLoader 在同一游戏目录共存 = 注入冲突，必崩
        if "BepInEx" in found and "MelonLoader" in found:
            issues.append(
                "检测到 BepInEx 与 MelonLoader 同时安装：两者都会注入游戏进程，"
                "共存会导致启动崩溃，请保留其中一个并移除另一个"
            )
        # 两个以上加载器共存（Fabric 与 Forge 类同）也值得提示
        if len(found) >= 2 and not ("BepInEx" in found and "MelonLoader" in found and len(found) == 2):
            issues.append(
                f"检测到多个 Mod 加载器共存（{'、'.join(found)}），"
                "若游戏出现崩溃请尝试只保留一个"
            )
        return issues

    def _scan_engine_keyfiles(self, root: Path, fp: TechStackFingerprint) -> list[str]:
        """按引擎类型检查关键文件是否齐全。"""
        issues: list[str] = []
        engine = (fp.engine.engine.lower() if fp.engine else "") or ""

        if engine == "unity":
            # Unity 游戏：根目录应有 <Game>_Data/Managed/Assembly-CSharp.dll
            data_dirs = list(root.glob("*_Data"))
            if data_dirs:
                managed = data_dirs[0] / "Managed" / "Assembly-CSharp.dll"
                if not managed.exists():
                    issues.append(
                        f"Unity 关键文件缺失：{data_dirs[0].name}/Managed/Assembly-CSharp.dll"
                        "不存在，安装可能不完整，建议在平台上验证文件完整性"
                    )
        elif engine == "unreal":
            # Unreal：*.pak 资源包缺失
            paks = list(root.rglob("*.pak"))
            if not paks:
                issues.append(
                    "未找到 Unreal 资源包（*.pak），安装可能不完整，"
                    "建议在平台上验证文件完整性"
                )
        return issues

    def _scan_file_integrity(self, root: Path) -> list[str]:
        """零字节文件 + 大小写冲突名（限深度/数量，避免大目录拖慢）。"""
        issues: list[str] = []
        zero_byte: list[str] = []
        seen_lower: dict[str, str] = {}  # 小写名 -> 原始名
        case_conflicts: list[str] = []

        scanned = 0
        for path in root.rglob("*"):
            scanned += 1
            if scanned > _MAX_SCAN_FILES:
                issues.append(f"目录文件数超过 {_MAX_SCAN_FILES}，完整性扫描已截断（大型 Mod 目录属正常）")
                break
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size == 0:
                zero_byte.append(path.name)
            # 大小写冲突：同目录下同名不同大小写
            key = f"{path.parent}|{path.name.lower()}"
            prev = seen_lower.get(key)
            if prev and prev != path.name:
                case_conflicts.append(f"{prev} / {path.name}")
            else:
                seen_lower[key] = path.name

        if zero_byte:
            sample = "、".join(zero_byte[:5])
            more = f" 等 {len(zero_byte)} 个" if len(zero_byte) > 5 else ""
            issues.append(
                f"发现 {len(zero_byte)} 个零字节文件（{sample}{more}），"
                "通常是下载中断或安全软件隔离所致，建议验证文件完整性"
            )
        if case_conflicts:
            sample = "；".join(case_conflicts[:3])
            issues.append(
                f"发现 {len(case_conflicts)} 组仅大小写不同的同名文件（{sample}），"
                "在 Linux/Proton 等区分大小写的系统上会互相覆盖"
            )
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
