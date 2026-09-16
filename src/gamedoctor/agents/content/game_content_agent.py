"""游戏内容管理智能体（game_content）。

合并原 ``update_manager`` / ``save_manager`` / ``dlc_manager``：存档备份与云同步、
游戏版本更新、DLC/Mod 三者都强挂钩同一条版本基线，故收归一个智能体按 ``op`` 分流。

任务分流（``data["op"]`` 显式优先，否则按任务名/能力推断，再否则 ``content`` 全做）：

- ``version``：版本对比、更新风险评估、回滚建议（只读）；
- ``saves``：存档清点 + 写操作前自动备份（沙箱模式下备份重定向 side 区）；
- ``dlc``：DLC/Mod 扫描、冲突检测、版本同步检查（只读扫描）；
- ``content``：三项全做，固定顺序 version → saves → dlc。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext
from ...sandbox import io as sxio

_VERSION_CAPS = {"update_manager", "updates", "version_check", "risk_assessment", "rollback"}
_SAVES_CAPS = {"save_manager", "saves", "cloud_sync", "backup_restore", "save_migration"}
_DLC_CAPS = {"dlc_manager", "dlc", "dlc_compatibility", "mod_installation",
             "conflict_detection", "version_sync"}


@dataclass
class VersionSnapshot:
    """版本与更新快照。"""

    current_version: str | None = None
    latest_version: str | None = None
    has_update: bool = False
    risk_level: str = "unknown"          # low / medium / high
    changelog: List[str] = field(default_factory=list)
    rollback_available: bool = True
    recommendations: List[str] = field(default_factory=list)


@dataclass
class SavesSnapshot:
    """存档管理快照。"""

    save_dir: Path | None = None
    backup_dir: Path | None = None
    saves: List[Dict[str, Any]] = field(default_factory=list)
    cloud_synced: bool = False
    recommendations: List[str] = field(default_factory=list)


@dataclass
class DlcSnapshot:
    """DLC/Mod 快照。"""

    installed_dlcs: List[str] = field(default_factory=list)
    installed_mods: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    compatibility_issues: List[str] = field(default_factory=list)
    sync_status: str = "unknown"          # synced / outdated / conflict
    recommendations: List[str] = field(default_factory=list)


@dataclass
class GameContentResult:
    """游戏内容管理结果（按 op 只填充对应分块）。"""

    game_name: str
    op: str = "content"
    version: Optional[VersionSnapshot] = None
    saves: Optional[SavesSnapshot] = None
    dlc: Optional[DlcSnapshot] = None


class GameContentAgent(BaseAgent):
    """游戏内容管理智能体：版本基线 / 存档备份 / DLC·Mod 同步。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("game_content", config)
        self.capabilities = [
            "game_content",
            # 更新/版本（原 update_manager）
            "update_manager", "updates", "version_check", "risk_assessment", "rollback",
            # 存档（原 save_manager）
            "save_manager", "saves", "cloud_sync", "backup_restore", "save_migration",
            # DLC/Mod（原 dlc_manager）
            "dlc_manager", "dlc", "dlc_compatibility", "mod_installation",
            "conflict_detection", "version_sync",
        ]
        # 更新
        self.auto_update = self.config.get("auto_update", True)
        self.beta_opt_in = self.config.get("beta_opt_in", False)
        # 存档
        self.auto_backup = self.config.get("auto_backup", True)
        self.cloud_sync = self.config.get("cloud_sync", True)
        # DLC/Mod
        self.conflict_detection = self.config.get("conflict_detection", True)
        self.mod_dir_name = self.config.get("mod_dir_name", "Mods")

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Game Content Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        data = task.data or {}
        game_name = data.get("game_name", "Unknown")
        context: GameContext = data.get("game_context")
        op = self._infer_op(task, data)

        try:
            result = GameContentResult(game_name=game_name, op=op)
            do = {"version": ("version",), "saves": ("saves",), "dlc": ("dlc",),
                  "content": ("version", "saves", "dlc")}[op]

            if "version" in do:
                result.version = self._analyze_version(game_name, context, data)
                self.record_metric("has_update", result.version.has_update)
            if "saves" in do:
                result.saves = self._manage_saves(self._sandbox(data), game_name, context, data)
                self.record_metric("save_count", len(result.saves.saves))
            if "dlc" in do:
                result.dlc = self._manage_dlc(game_name, context, data)
                self.record_metric("mod_count", len(result.dlc.installed_mods))
                self.record_metric("conflict_count", len(result.dlc.conflicts))

            return AgentResult(
                success=True,
                data=result,
                message=self._build_message(op, game_name, result),
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Game content management failed", e)
            return AgentResult(
                success=False,
                message=f"游戏内容管理失败：{e}",
                errors=[str(e)],
            )

    # ------------------------------------------------------------------ #
    # 任务分流
    # ------------------------------------------------------------------ #
    def _infer_op(self, task: AgentTask, data: Dict[str, Any]) -> str:
        """data["op"]/data["operation"] → 任务名关键词 → 能力标签 → 默认 content 全做。"""
        explicit = (data.get("op") or data.get("operation") or "").strip().lower()
        if explicit in ("version", "update", "updates", "update_management", "版本", "更新"):
            return "version"
        if explicit in ("saves", "save", "save_management", "backup", "存档", "备份"):
            return "saves"
        if explicit in ("dlc", "mod", "mods", "dlc_management", "模组"):
            return "dlc"
        if explicit in ("content", "all", "full", "全部"):
            return "content"

        name = (task.name or "").lower()
        if any(k in name for k in ("update", "version", "版本", "更新")):
            return "version"
        if any(k in name for k in ("save", "backup", "存档", "备份")):
            return "saves"
        if any(k in name for k in ("dlc", "mod", "模组")):
            return "dlc"

        caps = {c.lower() for c in (task.required_capabilities or [])}
        if caps & _VERSION_CAPS and not (caps & (_SAVES_CAPS | _DLC_CAPS)):
            return "version"
        if caps & _SAVES_CAPS and not (caps & (_VERSION_CAPS | _DLC_CAPS)):
            return "saves"
        if caps & _DLC_CAPS and not (caps & (_VERSION_CAPS | _SAVES_CAPS)):
            return "dlc"
        return "content"

    # ------------------------------------------------------------------ #
    # 版本与更新（原 update_manager 逻辑）
    # ------------------------------------------------------------------ #
    def _analyze_version(
        self, game_name: str, context: GameContext, data: Dict[str, Any]
    ) -> VersionSnapshot:
        current = (context.version if context else None) or data.get("current_version")
        latest = data.get("latest_version")
        has_update = bool(latest and latest != current)
        risk = self._assess_risk(latest)
        return VersionSnapshot(
            current_version=current,
            latest_version=latest,
            has_update=has_update,
            risk_level=risk,
            changelog=self._fetch_changelog(game_name, latest),
            rollback_available=True,
            recommendations=self._build_version_recommendations(has_update, risk),
        )

    def _assess_risk(self, latest_version: str | None) -> str:
        # TODO: 接入平台官方更新日志做真实风险评估
        if latest_version and latest_version.endswith(("beta", "rc")):
            return "high"
        return "low"

    def _fetch_changelog(self, game_name: str, latest_version: str | None) -> List[str]:
        # TODO: 从平台/官方源抓取更新日志
        return [f"{game_name} 更新到 {latest_version or '最新'}（占位）"]

    def _build_version_recommendations(self, has_update: bool, risk: str) -> List[str]:
        recs: List[str] = []
        if not has_update:
            recs.append("当前已是最新版本")
        elif risk == "high":
            recs.append("高风险更新，建议先备份再更新")
        else:
            recs.append("可安全更新")
        recs.append("建议在非游玩时段执行大版本更新")
        return recs

    # ------------------------------------------------------------------ #
    # 存档管理（原 save_manager 逻辑，保留沙箱备份重定向）
    # ------------------------------------------------------------------ #
    def _manage_saves(
        self, sx: Optional[Any], game_name: str,
        context: GameContext, data: Dict[str, Any]
    ) -> SavesSnapshot:
        save_dir = self._locate_save_dir(context, data)
        saves = self._list_saves(save_dir)
        backup_dir = None
        if self.auto_backup and save_dir:
            backup_dir = self._backup_saves(sx, game_name, save_dir)
        return SavesSnapshot(
            save_dir=save_dir,
            backup_dir=backup_dir,
            saves=saves,
            cloud_synced=self.cloud_sync,
            recommendations=self._build_saves_recommendations(save_dir, saves),
        )

    def _locate_save_dir(self, context: GameContext, data: Dict[str, Any]) -> Path | None:
        raw = data.get("save_path")
        if raw:
            return Path(raw)
        if context and context.install_path:
            candidate = context.install_path / "saves"
            if candidate.is_dir():
                return candidate
        return None

    def _list_saves(self, save_dir: Path | None) -> List[Dict[str, Any]]:
        if not save_dir or not save_dir.is_dir():
            return []
        saves = []
        for p in sorted(save_dir.iterdir()):
            if p.is_file():
                stat = p.stat()
                saves.append({
                    "name": p.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
        return saves

    def _backup_saves(self, sx: Optional[Any], game_name: str, save_dir: Path) -> Path:
        """备份存档到 ~/.gamedoctor/saves_backup（沙箱模式重定向 side 区）。

        存档可能是文件，也可能是「一个存档槽一个目录」（内含多文件/子目录），
        因此统一走 :func:`sxio.copy_path` 递归快照，文件与目录都不遗漏。
        """
        backup_root = Path.home() / ".gamedoctor" / "saves_backup" / game_name
        backup_dir = backup_root / datetime.now().strftime("%Y%m%d-%H%M%S")
        sxio.ensure_dir(sx, backup_dir, side=True)
        for p in save_dir.iterdir():
            sxio.copy_path(sx, p, backup_dir / p.name,
                           side_dst=True, pristine_src=True)
        return backup_dir

    def _build_saves_recommendations(
        self, save_dir: Path | None, saves: List[Dict[str, Any]]
    ) -> List[str]:
        recs: List[str] = []
        if not save_dir:
            recs.append("未定位到存档目录，请手动指定 save_path")
        if not saves:
            recs.append("未发现存档文件")
        if self.cloud_sync:
            recs.append("已启用云存档同步（如平台支持）")
        return recs

    # ------------------------------------------------------------------ #
    # DLC/Mod 管理（原 dlc_manager 逻辑）
    # ------------------------------------------------------------------ #
    def _manage_dlc(
        self, game_name: str, context: GameContext, data: Dict[str, Any]
    ) -> DlcSnapshot:
        install_path = self._resolve_install_path(context, data)
        dlcs = self._scan_dlcs(install_path)
        mods = self._scan_mods(install_path)
        conflicts = self._detect_conflicts(mods) if self.conflict_detection else []
        issues = self._check_dlc_compatibility(mods, context)
        sync_status = self._version_sync_status(mods)
        return DlcSnapshot(
            installed_dlcs=dlcs,
            installed_mods=mods,
            conflicts=conflicts,
            compatibility_issues=issues,
            sync_status=sync_status,
            recommendations=self._build_dlc_recommendations(conflicts, issues, sync_status),
        )

    def _resolve_install_path(self, context: GameContext, data: Dict[str, Any]) -> Path | None:
        if context and context.install_path:
            return context.install_path
        raw = data.get("install_path")
        return Path(raw) if raw else None

    def _scan_dlcs(self, install_path: Path | None) -> List[str]:
        """扫描已安装的 DLC（简化：读取安装目录下 DLC/ 子目录名）。"""
        if install_path is None:
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
            mods = sorted(p.name for p in install_path.glob("*")
                          if p.suffix.lower() in (".esp", ".pak", ".jar"))
        return mods

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
        self, mods: List[str], context: GameContext
    ) -> List[str]:
        issues: List[str] = []
        version = (context.version or "") if context else ""
        for mod in mods:
            # 简化：游戏主版本更新后，旧 Mod 通常不兼容
            if version and version.startswith("2."):
                issues.append(f"Mod [{mod}] 可能不兼容当前游戏版本 {version}")
        return issues

    def _version_sync_status(self, mods: List[str]) -> str:
        if not mods:
            return "synced"
        return "synced"

    def _build_dlc_recommendations(
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

    # ------------------------------------------------------------------ #
    # 汇总话术
    # ------------------------------------------------------------------ #
    def _build_message(self, op: str, game_name: str, result: GameContentResult) -> str:
        scope = {"version": "版本检查", "saves": "存档管理",
                 "dlc": "DLC/Mod 管理", "content": "游戏内容管理"}[op]
        parts: List[str] = []
        if result.version is not None:
            parts.append("有更新" if result.version.has_update else "版本最新")
        if result.saves is not None:
            parts.append(f"存档 {len(result.saves.saves)} 个")
        if result.dlc is not None:
            parts.append(f"Mod {len(result.dlc.installed_mods)} 个/"
                         f"冲突 {len(result.dlc.conflicts)} 项")
        tail = f"（{'；'.join(parts)}）" if parts else ""
        return f"{game_name} {scope}完成{tail}"
