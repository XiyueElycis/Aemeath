"""游戏安装规划智能体。

智能规划安装流程，优化安装路径，管理下载源等。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from ..base_agent import BaseAgent, AgentTask, AgentResult
from ...fingerprint import detect_platform
from ...models import GameContext, PlatformInfo
from ...sandbox import io as sxio

# 最大 exe 兜底策略要排除的安装器/运行库/卸载程序关键词（小写匹配）
_EXE_EXCLUDE = ("unins", "setup", "install", "redist", "vcredist", "dxsetup",
                "crashreport", "unitycrashhandler", "dotnet",
                "prerequisites", "commonredist")

# 固定主程序名兜底（同名 exe 没命中时使用；仅查顶层）
_COMMON_EXE_NAMES = ("game.exe", "start.exe", "main.exe", "launcher.exe",
                     "play.exe", "run.exe")


class InstallationAgent(BaseAgent):
    """安装规划智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("installation", config)
        self.capabilities = ["installation", "installation_planning", "path_optimization", "source_selection"]
        self.preferred_sources = self.config.get("preferred_sources", ["steam", "epic", "gog"])
        self.auto_verify = self.config.get("auto_verify", True)

    def _do_initialize(self) -> None:
        """初始化智能体。"""
        self.logger.info("Initializing Installation Agent")
        # 加载已知安装路径映射
        self._load_path_mappings()

    def _load_path_mappings(self) -> None:
        """加载已知的安装路径映射。"""
        self.path_mappings = {
            "steam": {
                "default_paths": [
                    Path(r"C:\Program Files (x86)\Steam\steamapps\common"),
                    Path(r"D:\Games\Steam\steamapps\common"),
                    Path(r"E:\Steam\steamapps\common")
                ]
            },
            "epic": {
                "default_paths": [
                    Path(r"C:\Program Files (x86)\Epic Games\Launcher\Installed"),
                    Path(r"D:\Games\Epic Games\Launcher\Installed")
                ]
            },
            "standalone": {
                "default_paths": [
                    Path(r"C:\Program Files"),
                    Path(r"D:\Games"),
                    Path(r"E:\Games")
                ]
            }
        }

    async def execute(self, task: AgentTask) -> AgentResult:
        """执行安装规划任务。"""
        try:
            task_data = task.data or {}
            game_name = task_data.get("game_name", "")
            install_source = task_data.get("source", "auto")

            # 已装游戏目录：task.context（治理链路权威路径）优先，其次 data.game_dir
            gc: GameContext | None = getattr(task, "context", None)
            game_dir = task_data.get("game_dir", "") or ""
            if gc is not None and gc.install_path:
                game_dir = str(gc.install_path)
            existing_game = game_dir and Path(game_dir).is_dir()

            # 平台只探测一次（appmanifest 扫描有成本），供源选择与计划备注复用
            platform_info = self._detect_platform(game_name, game_dir)

            # 1. 选择安装源
            source = self._select_source(game_name, install_source,
                                         platform_info.platform)

            # 2. 优化安装路径（已装游戏原样使用传入目录，绝不重算到 ~/Games）
            install_path = self._optimize_install_path(
                game_name, source, self._sandbox(task_data), game_dir=game_dir,
            )

            # 3. 检查磁盘空间
            space_check = self._check_disk_space(install_path)

            # 4. 生成安装计划
            plan = self._generate_install_plan(game_name, source, install_path, space_check)
            plan["platform"] = platform_info.platform
            plan["existing_game"] = bool(existing_game)
            if existing_game and source == "standalone" and install_source == "auto":
                plan["pre_install_checks"].insert(
                    0, "未能从 Steam/Epic/GOG 清单确认平台归属，按独立版处理（如判断有误请指定平台）"
                )

            # 5. 自动验证（如果启用）
            if self.auto_verify:
                verification = await self._verify_installation(game_name, install_path)
                plan["verification"] = verification

            # 记录指标
            self.record_metric("install_path_size", plan.get("estimated_size", 0))
            self.record_metric("install_time_estimate", plan.get("estimated_time", 0))

            return AgentResult(
                success=True,
                data=plan,
                message=f"Installation plan generated for {game_name}"
            )

        except Exception as e:
            self.log_error("Installation planning failed", e)
            return AgentResult(
                success=False,
                message=f"Installation planning failed: {str(e)}",
                errors=[str(e)]
            )

    def _detect_platform(self, game_name: str, game_dir: str = "") -> PlatformInfo:
        """探测平台归属；探测失败降级为 standalone，不阻断安装规划。"""
        try:
            search_root = Path(game_dir) if game_dir and Path(game_dir).is_dir() else None
            return detect_platform(game_name, search_root)
        except Exception:  # noqa: BLE001
            self.logger.warning("平台探测失败，按 standalone 处理", exc_info=True)
            return PlatformInfo()

    def _select_source(self, game_name: str, preferred_source: str,
                       detected_platform: str = "standalone") -> str:
        """选择安装源。"""
        if preferred_source == "auto":
            # 平台指纹已由调用方探测一次，直接采信，不再恒 True 猜源
            if detected_platform in ("steam", "epic", "gog"):
                return detected_platform
            return "standalone"
        else:
            # 使用指定的源
            return preferred_source

    def _is_game_available(self, game_name: str, source: str) -> bool:
        """检查游戏在指定源是否可用。"""
        if source == "steam":
            # 检查 Steam
            return self._check_steam_game(game_name)
        elif source == "epic":
            # 检查 Epic
            return self._check_epic_game(game_name)
        else:
            return True  # 独立版总是可用

    def _optimize_install_path(self, game_name: str, source: str, sx=None,
                               game_dir: str = "") -> Path:
        """优化安装路径（沙箱模式下建目录重定向 side 区，审核应用时才真实创建）。

        已装游戏（传入的 game_dir 真实存在）一律原样返回——这是"对已有安装做
        排查/修复"场景的主路径，绝不能重算到 ``~/Games`` 造成找不到主程序。
        """
        if game_dir:
            given = Path(game_dir)
            if given.is_dir():
                return given

        # 获取候选路径
        if source in self.path_mappings:
            candidate_paths = self.path_mappings[source]["default_paths"]
        else:
            candidate_paths = self.path_mappings["standalone"]["default_paths"]

        # 选择最佳路径
        for path in candidate_paths:
            if path.exists() and self._has_enough_space(path, 10 * 1024 * 1024 * 1024):  # 10GB
                return path / game_name

        # 如果都没有合适的，创建默认路径
        default_path = Path.home() / "Games"
        sxio.ensure_dir(sx, default_path, side=True)
        return default_path / game_name

    def _check_disk_space(self, install_path: Path) -> Dict[str, Any]:
        """检查磁盘空间。"""
        try:
            disk_usage = shutil.disk_usage(install_path.parent)
            return {
                "total_space": disk_usage.total,
                "free_space": disk_usage.free,
                "used_space": disk_usage.used,
                "enough": disk_usage.free > 10 * 1024 * 1024 * 1024  # 至少 10GB
            }
        except Exception:
            return {
                "total_space": 0,
                "free_space": 0,
                "used_space": 0,
                "enough": False
            }

    def _generate_install_plan(self, game_name: str, source: str, install_path: Path, space_check: Dict[str, Any]) -> Dict[str, Any]:
        """生成安装计划。"""
        # 估算安装大小（根据游戏类型）
        estimated_size = self._estimate_game_size(game_name, source)

        # 估算安装时间
        estimated_time = self._estimate_install_time(estimated_size)

        return {
            "game_name": game_name,
            "install_source": source,
            "install_path": str(install_path),
            "estimated_size": estimated_size,
            "estimated_time": estimated_time,
            "disk_check": space_check,
            "pre_install_checks": self._generate_pre_checks(game_name, install_path),
            "post_install_tasks": self._generate_post_tasks(game_name, source),
            "recommendations": self._generate_recommendations(game_name)
        }

    def _generate_pre_checks(self, game_name: str, install_path: Path) -> List[str]:
        """生成预安装检查清单。"""
        checks = []

        # 检查路径是否已存在
        if install_path.exists():
            checks.append("警告：安装路径已存在文件，可能覆盖")

        # 检查权限
        if not os.access(install_path.parent, os.W_OK):
            checks.append("警告：没有写入权限")

        # 检查依赖
        checks.extend(self._check_dependencies(game_name))

        return checks

    def _generate_post_tasks(self, game_name: str, source: str) -> List[str]:
        """生成安装后任务。"""
        tasks = []

        # 添加游戏到启动器
        if source in ["steam", "epic"]:
            tasks.append("添加游戏到对应平台")

        # 优化设置
        tasks.append("优化游戏设置")

        # 创建快捷方式
        tasks.append("创建桌面快捷方式")

        return tasks

    def _generate_recommendations(self, game_name: str) -> List[str]:
        """生成建议。"""
        recommendations = []

        # 根据游戏类型给出建议
        if self._is_graphic_intensive(game_name):
            recommendations.append("建议更新显卡驱动")
            recommendations.append("建议关闭后台程序以获得更好性能")

        if self._is_online_game(game_name):
            recommendations.append("确保网络连接稳定")

        return recommendations

    def _estimate_game_size(self, game_name: str, source: str) -> int:
        """估算游戏大小。"""
        # 根据游戏类型和源估算
        base_sizes = {
            "AAA": 50 * 1024 * 1024 * 1024,  # 50GB
            "Indie": 5 * 1024 * 1024 * 1024,   # 5GB
            "Strategy": 20 * 1024 * 1024 * 1024  # 20GB
        }

        game_type = self._classify_game_type(game_name)
        return base_sizes.get(game_type, 10 * 1024 * 1024 * 1024)

    def _estimate_install_time(self, size_bytes: int) -> int:
        """估算安装时间。"""
        # 假设平均下载速度 10MB/s
        download_time = size_bytes / (10 * 1024 * 1024)
        # 解压时间估算
        extract_time = size_bytes / (50 * 1024 * 1024)  # 50MB/s

        return int(download_time + extract_time)

    def _has_enough_space(self, path: Path, required_bytes: int) -> bool:
        """检查是否有足够空间。"""
        try:
            disk_usage = shutil.disk_usage(path)
            return disk_usage.free >= required_bytes
        except Exception:
            return False

    def _check_dependencies(self, game_name: str) -> List[str]:
        """检查依赖。"""
        dependencies = []

        # 检查 DirectX
        if self._needs_directx(game_name):
            dependencies.append("DirectX 运行时")

        # 检查 .NET Framework
        if self._needs_dotnet(game_name):
            dependencies.append(".NET Framework")

        # 检查 Visual C++
        if self._needs_vc_redist(game_name):
            dependencies.append("Visual C++ Redistributable")

        return dependencies

    def _classify_game_type(self, game_name: str) -> str:
        """分类游戏类型。"""
        # 简化的分类逻辑
        if any(keyword in game_name.lower() for keyword in ["call of duty", "battlefield", "assassin"]):
            return "AAA"
        elif any(keyword in game_name.lower() for keyword in ["minecraft", "terraria", "stardew"]):
            return "Indie"
        elif any(keyword in game_name.lower() for keyword in ["civilization", "total war", "age"]):
            return "Strategy"
        return "Indie"

    def _is_graphic_intensive(self, game_name: str) -> bool:
        """判断是否是图形密集型游戏。"""
        return any(keyword in game_name.lower() for keyword in ["assassin", "battlefield", "call of duty"])

    def _is_online_game(self, game_name: str) -> bool:
        """判断是否是网络游戏。"""
        return any(keyword in game_name.lower() for keyword in ["online", "multiplayer", "battle royale"])

    def _needs_directx(self, game_name: str) -> bool:
        """检查是否需要 DirectX。"""
        # 简化处理
        return True

    def _needs_dotnet(self, game_name: str) -> bool:
        """检查是否需要 .NET Framework。"""
        # 简化处理
        return ".net" in game_name.lower()

    def _needs_vc_redist(self, game_name: str) -> bool:
        """检查是否需要 VC Redistributable。"""
        # 简化处理
        return True

    async def _verify_installation(self, game_name: str, install_path: Path) -> Dict[str, Any]:
        """验证安装是否成功。"""
        verification = {
            "success": False,
            "checks": [],
            "issues": []
        }

        try:
            # 检查主程序
            main_exe = self._find_main_executable(install_path)
            if main_exe:
                verification["checks"].append(f"找到主程序: {main_exe}")
            else:
                verification["issues"].append("未找到主程序")

            # 检查配置文件
            config_files = self._find_config_files(install_path)
            if config_files:
                verification["checks"].append(f"找到配置文件: {len(config_files)} 个")

            # 检查完整性
            integrity_check = await self._check_integrity(install_path)
            verification["integrity_check"] = integrity_check

            verification["success"] = len(verification["issues"]) == 0

        except Exception as e:
            verification["issues"].append(f"验证失败: {str(e)}")

        return verification

    def _find_main_executable(self, install_path: Path) -> Path | None:
        """查找主程序，三策略逐级兜底：

        1. 目录同名 exe（``TestGame/TestGame.exe``，绝大多数商业游戏的命名约定）；
        2. 常见固定名（game/start/launcher/play/run）；
        3. 顶层最大的 exe（排除卸载器/安装器/运行库分发程序）。
        """
        if not install_path.is_dir():
            return None

        try:
            top_files = [p for p in install_path.iterdir() if p.is_file()]
        except OSError:
            return None

        # 策略 1：目录同名 exe（大小写不敏感，Windows 常态）
        target_stem = install_path.name.lower()
        for f in top_files:
            if f.suffix.lower() == ".exe" and f.stem.lower() == target_stem:
                return f

        # 策略 2：常见固定名
        lower_map = {f.name.lower(): f for f in top_files if f.suffix.lower() == ".exe"}
        for name in _COMMON_EXE_NAMES:
            if name in lower_map:
                return lower_map[name]

        # 策略 3：顶层最大 exe，排除安装器/卸载器/运行库
        candidates = [
            f for f in top_files
            if f.suffix.lower() == ".exe"
            and not any(k in f.name.lower() for k in _EXE_EXCLUDE)
        ]
        if candidates:
            try:
                return max(candidates, key=lambda p: p.stat().st_size)
            except OSError:
                return candidates[0]
        return None

    def _find_config_files(self, install_path: Path) -> List[Path]:
        """查找配置文件。"""
        config_extensions = [".ini", ".cfg", ".xml", ".json"]
        config_files = []

        for file in install_path.rglob("*"):
            if file.suffix in config_extensions:
                config_files.append(file)

        return config_files

    async def _check_integrity(self, install_path: Path) -> Dict[str, Any]:
        """检查文件完整性。"""
        # 简化实现，实际应该计算文件哈希
        return {
            "files_count": len(list(install_path.rglob("*"))),
            "total_size": sum(f.stat().st_size for f in install_path.rglob("*") if f.is_file()),
            "missing_files": []
        }

    def _check_steam_game(self, game_name: str) -> bool:
        """游戏是否归属 Steam：以 appmanifest 平台探测为准，不再恒 True。"""
        try:
            return self._detect_platform(game_name).platform == "steam"
        except Exception:  # noqa: BLE001
            return False

    def _check_epic_game(self, game_name: str) -> bool:
        """游戏是否归属 Epic：以平台探测为准，不再恒 True。"""
        try:
            return self._detect_platform(game_name).platform == "epic"
        except Exception:  # noqa: BLE001
            return False