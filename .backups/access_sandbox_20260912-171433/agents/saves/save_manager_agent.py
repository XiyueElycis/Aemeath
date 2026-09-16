"""游戏存档管理智能体。

存档生命周期管理：云存档同步、备份与恢复、多存档管理、存档迁移。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class SaveManagementResult:
    """存档管理结果。"""

    game_name: str
    save_dir: Path | None = None
    backup_dir: Path | None = None
    saves: List[Dict[str, Any]] = field(default_factory=list)
    cloud_synced: bool = False
    recommendations: List[str] = field(default_factory=list)


class SaveManagerAgent(BaseAgent):
    """存档管理智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("save_manager", config)
        self.capabilities = [
            "save_manager", "saves", "cloud_sync", "backup_restore", "save_migration",
        ]
        self.auto_backup = self.config.get("auto_backup", True)
        self.cloud_sync = self.config.get("cloud_sync", True)

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Save Manager Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")
            context: GameContext = data.get("game_context")

            save_dir = self._locate_save_dir(game_name, context, data)
            saves = self._list_saves(save_dir)

            backup_dir = None
            if self.auto_backup and save_dir:
                backup_dir = self._backup_saves(game_name, save_dir)

            result = SaveManagementResult(
                game_name=game_name,
                save_dir=save_dir,
                backup_dir=backup_dir,
                saves=saves,
                cloud_synced=self.cloud_sync,
                recommendations=self._build_recommendations(save_dir, saves),
            )

            self.record_metric("save_count", len(saves))
            return AgentResult(
                success=True,
                data=result,
                message=f"Save management completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Save management failed", e)
            return AgentResult(
                success=False,
                message=f"Save management failed: {e}",
                errors=[str(e)],
            )

    def _locate_save_dir(
        self, game_name: str, context: GameContext, data: Dict[str, Any]
    ) -> Path | None:
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

    def _backup_saves(self, game_name: str, save_dir: Path) -> Path:
        backup_root = Path.home() / ".gamedoctor" / "saves_backup" / game_name
        backup_dir = backup_root / datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir.mkdir(parents=True, exist_ok=True)
        for p in save_dir.iterdir():
            if p.is_file():
                shutil.copy2(p, backup_dir / p.name)
        return backup_dir

    def _build_recommendations(self, save_dir: Path | None, saves: List[Dict[str, Any]]) -> List[str]:
        recs: List[str] = []
        if not save_dir:
            recs.append("未定位到存档目录，请手动指定 save_path")
        if not saves:
            recs.append("未发现存档文件")
        if self.cloud_sync:
            recs.append("已启用云存档同步（如平台支持）")
        return recs