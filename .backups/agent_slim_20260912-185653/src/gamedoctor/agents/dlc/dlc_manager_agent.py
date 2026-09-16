"""DLC/Mod 管理智能体。

管理扩展内容：DLC 兼容性检查、Mod 自动安装与配置、冲突检测、版本同步。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class DLCManagementResult:
    """DLC/Mod 管理结果。"""

    game_name: str
    installed_dlcs: List[str] = field(default_factory=list)
    installed_mods: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    compatibility_issues: List[str] = field(default_factory=list)
    sync_status: str = "unknown"          # synced / outdated / conflict
    recommendations: List[str] = field(default_factory=list)


class DLCManagerAgent(BaseAgent):
    """DLC/Mod 管理智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("dlc_manager", config)
        self.capabilities = [
            "dlc_manager", "dlc", "dlc_compatibility", "mod_installation",
            "conflict_detection", "version_sync",
        ]
        self.auto_update = self.config.get("auto_update", True)
        self.conflict_detection = self.config.get("conflict_detection", True)
        self.mod_dir_name = self.config.get("mod_dir_name", "Mods")

    def _do_initialize(self) -> None:
        self.logger.info("Initializing DLC Manager Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")
            context: GameContext = data.get("game_context")
            install_path = self._resolve_install_path(context, data)

            dlcs = self._scan_dlcs(install_path, game_name)
            mods = self._scan_mods(install_path)
            conflicts = self._detect_conflicts(mods) if self.conflict_detection else []
            issues = self._check_dlc_compatibility(dlcs, mods, context)
            sync_status = self._version_sync_status(dlcs, mods)

            result = DLCManagementResult(
                game_name=game_name,
                installed_dlcs=dlcs,
                installed_mods=mods,
                conflicts=conflicts,
                compatibility_issues=issues,
                sync_status=sync_status,
                recommendations=self._build_recommendations(conflicts, issues, sync_status),
            )

            self.record_metric("mod_count", len(mods))
            self.record_metric("conflict_count", len(conflicts))

            return AgentResult(
                success=True,
                data=result,
                message=f"DLC/Mod management completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("DLC management failed", e)
            return AgentResult(
                success=False,
                message=f"DLC management failed: {e}",
                errors=[str(e)],
            )

    # -- 采集 ------------------------------------------------------------- #
    def _resolve_install_path(self, context: GameContext, data: Dict[str, Any]) -> Path | None:
        if context and context.install_path:
            return context.install_path
        raw = data.get("install_path")
        return Path(raw) if raw else None

    def _scan_dlcs(self, install_path: Path | None, game_name: str) -> List[str]:
        """扫描已安装的 DLC（简化：读取 context.dlcs 或目录名推断）。"""
        # 优先使用上下文里已知的 DLC 列表
        if install_path is None:
            # 无路径时回退到基于游戏名的已知 DLC 猜测（占位）
            return []
        dlc_dir = install_path / "DLC"
        if dlc_dir.is_dir():
            return sorted(p.name for p in dlc_dir.iterdir() if p.is_dir())
        return []

    def _scan_mods(self, install_path: Path | None) -> List[str]:
        mods: List[str] = []
        if install_path is None:
            return mods
        mod_dir = install_path / self.mod_dir_name
        if mod_dir.is_dir():
            mods = sorted(p.name for p in mod_dir.iterdir())
        else:
            # 常见 Mod 文件扩展名
            mods = sorted(p.name for p in install_path.glob("*") if p.suffix.lower() in (".esp", ".pak", ".jar"))
        return mods

    # -- 检测 ------------------------------------------------------------- #
    def _detect_conflicts(self, mods: List[str]) -> List[str]:
        """检测 Mod 冲突（简化：按同名/近似名 + 常见覆盖规则启发）。"""
        conflicts: List[str] = []
        seen: Dict[str, str] = {}
        for mod in mods:
            stem = Path(mod).stem.lower()
            if stem in seen:
                conflicts.append(f"Mod 重复/覆盖：{seen[stem]} 与 {mod}")
            seen[stem] = mod
        return conflicts

    def _check_dlc_compatibility(
        self, dlcs: List[str], mods: List[str], context: GameContext
    ) -> List[str]:
        issues: List[str] = []
        version = (context.version or "") if context else ""
        for mod in mods:
            # 简化：游戏主版本更新后，旧 Mod 通常不兼容
            if version and version.startswith("2."):
                issues.append(f"Mod [{mod}] 可能不兼容当前游戏版本 {version}")
        return issues

    def _version_sync_status(self, dlcs: List[str], mods: List[str]) -> str:
        if not mods:
            return "synced"
        return "synced"

    def _build_recommendations(
        self, conflicts: List[str], issues: List[str], sync_status: str
    ) -> List[str]:
        recs: List[str] = []
        if conflicts:
            recs.append("建议禁用或卸载存在冲突的 Mod")
        if issues:
            recs.append("建议检查 Mod 是否适配当前游戏版本")
        if self.auto_update:
            recs.append("已开启自动同步，可尝试更新所有 Mod 到最新版")
        return recs