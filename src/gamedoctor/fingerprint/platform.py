"""平台适配层（PRD §4.1 平台适配层）。

区分 Steam / Epic / GOG / 独立版：不同平台日志位置与修复手段不同
（Steam verify integrity vs Epic repair）。

识别依据（均为本地特征文件/注册表，不联网）：

- **Steam**：注册表/默认安装路径定位 Steam → 读 ``config/libraryfolders.vdf``
  得到所有库目录 → 库内 ``steamapps/appmanifest_<appid>.acf`` 的
  ``name`` 字段匹配游戏名，安装目录为 ``steamapps/common/<installdir>``。
- **Epic**：``%ProgramData%\\Epic\\EpicGamesLauncher\\Data\\Manifests\\*.item``
  （JSON）的 ``DisplayName`` / ``InstallLocation`` / ``AppName``。
- **GOG**：游戏目录内的 ``goggame-<appid>.info`` 文件；或
  ``%ProgramData%\\GOG.com\\Galaxy\\storage\\index`` 索引。

所有平台探测逐项容错；任何一项失败都降级为 ``standalone``，不抛异常。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional
from typing import Protocol

from ..models import PlatformInfo


class PlatformAdapter(Protocol):
    """平台适配器协议。

    定义"平台识别"的最小接口，用于区分 Steam / Epic / GOG / 独立版等。
    """

    def detect(self, game_name: str, search_root: Path | None = None) -> PlatformInfo:
        """根据游戏名 / 安装位置识别所属平台。

        :param game_name: 游戏名称。
        :param search_root: 可选的安装根目录，用于定位平台特征文件；为 None 时由实现自行决定。
        :return: 识别结果 ``PlatformInfo``（平台类型 + 相应修复手段）。
        """
        ...


def _norm(name: str) -> str:
    """归一化游戏名用于模糊匹配：小写 + 去除非字母数字。"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (name or "").lower())


def _fuzzy_match(game_name: str, candidate: str) -> bool:
    """游戏名模糊匹配：归一化后双向包含。

    覆盖「Steam 清单写 "Cyberpunk 2077"、用户输入 "赛博朋克 2077"」之外
    的常见英文场景（"Stardew Valley" vs "stardewvalley"）；中文名仍需用户
    传入与清单一致的名字。
    """
    g, c = _norm(game_name), _norm(candidate)
    if not g or not c:
        return False
    return g in c or c in g


class DefaultPlatformAdapter:
    """本地特征文件探测实现（Steam / Epic / GOG → standalone 兜底）。"""

    def detect(self, game_name: str, search_root: Path | None = None) -> PlatformInfo:
        # 1. 显式给出安装根目录：先看目录本身/上级有没有平台特征
        if search_root is not None:
            hit = self._detect_in_root(game_name, Path(search_root))
            if hit:
                return hit

        # 2. Steam：库清单最可靠，优先
        hit = self._detect_steam(game_name)
        if hit:
            return hit

        # 3. Epic：Launcher 清单
        hit = self._detect_epic(game_name)
        if hit:
            return hit

        # 4. GOG：Galaxy 索引 / 游戏目录特征
        hit = self._detect_gog(game_name, search_root)
        if hit:
            return hit

        return PlatformInfo(platform="standalone", install_path=search_root)

    # ------------------------------------------------------------------ #
    def _detect_in_root(self, game_name: str, root: Path) -> Optional[PlatformInfo]:
        """在给定安装目录内识别平台特征（GOG 的 goggame 文件 / Steam 上层路径）。"""
        root = root.resolve() if root.exists() else root
        # GOG：游戏目录里有 goggame-*.info
        for info in root.glob("goggame-*.info"):
            m = re.match(r"goggame-(\d+)\.info", info.name)
            return PlatformInfo(
                platform="gog", app_id=m.group(1) if m else None,
                install_path=root,
            )
        # Steam：目录位于 <library>/steamapps/common/<game> 下
        parts_lower = [p.lower() for p in root.parts]
        if "steamapps" in parts_lower and "common" in parts_lower:
            # 向上找 steamapps 目录，再查同级 appmanifest
            for parent in [root, *root.parents]:
                if parent.name.lower() == "common" and parent.parent.name.lower() == "steamapps":
                    steamapps = parent.parent
                    return self._steam_manifest_in(steamapps, game_name, fallback_path=root)
        # Epic：目录同级或上级有 .egstore
        for parent in [root, *root.parents][:4]:
            if (parent / ".egstore").exists():
                return PlatformInfo(platform="epic", install_path=root)
        return None

    # ------------------------------------------------------------------ #
    def _steam_roots(self) -> list[Path]:
        """定位 Steam 安装目录（注册表 → 默认路径）。"""
        roots: list[Path] = []
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
                ) as key:
                    value, _ = winreg.QueryValueEx(key, "SteamPath")
                    if value:
                        roots.append(Path(value))
            except OSError:
                pass
        candidates = [
            Path(r"C:\Program Files (x86)\Steam"),
            Path(r"C:\Program Files\Steam"),
            Path.home() / ".steam" / "steam",
            Path.home() / ".local" / "share" / "Steam",
        ]
        for c in candidates:
            if c.exists() and (c / "steamapps").exists():
                roots.append(c)
        # 去重保序
        seen: set[str] = set()
        out: list[Path] = []
        for r in roots:
            rp = str(r)
            if rp not in seen:
                seen.add(rp)
                out.append(r)
        return out

    def _steam_library_dirs(self, steam_root: Path) -> list[Path]:
        """解析 libraryfolders.vdf 拿到全部库目录（含默认库）。"""
        libs = [steam_root]
        vdf = steam_root / "config" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return libs
        # vdf 里每行形如 "path"  "D:\\SteamLibrary"
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            p = Path(m.group(1).replace("\\\\", "\\"))
            if p.exists():
                libs.append(p)
        return libs

    def _detect_steam(self, game_name: str) -> Optional[PlatformInfo]:
        for steam_root in self._steam_roots():
            for lib in self._steam_library_dirs(steam_root):
                steamapps = lib / "steamapps"
                if not steamapps.exists():
                    continue
                hit = self._steam_manifest_in(steamapps, game_name)
                if hit:
                    return hit
        return None

    def _steam_manifest_in(
        self, steamapps: Path, game_name: str, fallback_path: Path | None = None
    ) -> Optional[PlatformInfo]:
        """在某个 steamapps 目录里用 appmanifest 匹配游戏名。"""
        for acf in steamapps.glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            name_m = re.search(r'"name"\s+"([^"]+)"', text)
            dir_m = re.search(r'"installdir"\s+"([^"]+)"', text)
            appid_m = re.search(r'"appid"\s+"?(\d+)"?', text)
            if not name_m or not _fuzzy_match(game_name, name_m.group(1)):
                continue
            install_dir = dir_m.group(1) if dir_m else name_m.group(1)
            install_path = steamapps / "common" / install_dir
            if not install_path.exists() and fallback_path is not None:
                install_path = fallback_path
            return PlatformInfo(
                platform="steam",
                app_id=appid_m.group(1) if appid_m else None,
                install_path=install_path if install_path.exists() else fallback_path,
                library=str(steamapps.parent),
            )
        return None

    # ------------------------------------------------------------------ #
    def _epic_manifest_dir(self) -> Optional[Path]:
        """Epic Launcher 清单目录（*.item，JSON）。"""
        if sys.platform == "win32":
            program_data = os.environ.get("ProgramData", r"C:\ProgramData")
            d = Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
            if d.exists():
                return d
        # macOS 的 Epic 清单（兜底）
        mac_d = Path.home() / "Library" / "Application Support" / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        return mac_d if mac_d.exists() else None

    def _detect_epic(self, game_name: str) -> Optional[PlatformInfo]:
        manifest_dir = self._epic_manifest_dir()
        if manifest_dir is None:
            return None
        for item in manifest_dir.glob("*.item"):
            try:
                data = json.loads(item.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, json.JSONDecodeError):
                continue
            display = str(data.get("DisplayName", ""))
            if not display or not _fuzzy_match(game_name, display):
                continue
            loc = data.get("InstallLocation")
            return PlatformInfo(
                platform="epic",
                app_id=str(data.get("AppName", "")) or None,
                install_path=Path(loc) if loc and Path(loc).exists() else None,
            )
        return None

    # ------------------------------------------------------------------ #
    def _detect_gog(self, game_name: str, search_root: Path | None) -> Optional[PlatformInfo]:
        """GOG：优先扫安装根目录；无根目录时无法定位（Galaxy 索引为 SQLite，
        需要额外解析，此处不做猜测性全盘扫描）。"""
        if search_root is None:
            return None
        root = Path(search_root)
        if not root.exists():
            return None
        for info in root.glob("goggame-*.info"):
            m = re.match(r"goggame-(\d+)\.info", info.name)
            return PlatformInfo(
                platform="gog", app_id=m.group(1) if m else None, install_path=root,
            )
        return None


def detect_platform(game_name: str, search_root: Path | None = None) -> PlatformInfo:
    """便捷函数：用默认适配器识别游戏所属平台。"""
    return DefaultPlatformAdapter().detect(game_name, search_root)
