"""游戏更新管理智能体。

智能更新管理：版本对比、更新日志、更新风险评估、回滚机制、时间规划。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class UpdateManagementResult:
    """更新管理结果。"""

    game_name: str
    current_version: str | None = None
    latest_version: str | None = None
    has_update: bool = False
    risk_level: str = "unknown"          # low / medium / high
    changelog: List[str] = field(default_factory=list)
    rollback_available: bool = True
    recommendations: List[str] = field(default_factory=list)


class UpdateManagerAgent(BaseAgent):
    """更新管理智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("update_manager", config)
        self.capabilities = ["update_manager", "updates", "version_check", "risk_assessment", "rollback"]
        self.auto_update = self.config.get("auto_update", True)
        self.beta_opt_in = self.config.get("beta_opt_in", False)

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Update Manager Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")
            context: GameContext = data.get("game_context")

            current = (context.version if context else None) or data.get("current_version")
            latest = data.get("latest_version")

            has_update = bool(latest and latest != current)
            risk = self._assess_risk(game_name, latest)

            result = UpdateManagementResult(
                game_name=game_name,
                current_version=current,
                latest_version=latest,
                has_update=has_update,
                risk_level=risk,
                changelog=self._fetch_changelog(game_name, latest),
                rollback_available=True,
                recommendations=self._build_recommendations(has_update, risk),
            )

            self.record_metric("has_update", has_update)
            return AgentResult(
                success=True,
                data=result,
                message=f"Update management completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Update management failed", e)
            return AgentResult(
                success=False,
                message=f"Update management failed: {e}",
                errors=[str(e)],
            )

    def _assess_risk(self, game_name: str, latest_version: str | None) -> str:
        # TODO: 接入平台官方更新日志做真实风险评估
        if latest_version and latest_version.endswith(("beta", "rc")):
            return "high"
        return "low"

    def _fetch_changelog(self, game_name: str, latest_version: str | None) -> List[str]:
        # TODO: 从平台/官方源抓取更新日志
        return [f"{game_name} 更新到 {latest_version or '最新'}（占位）"]

    def _build_recommendations(self, has_update: bool, risk: str) -> List[str]:
        recs: List[str] = []
        if not has_update:
            recs.append("当前已是最新版本")
        elif risk == "high":
            recs.append("高风险更新，建议先备份再更新")
        else:
            recs.append("可安全更新")
        recs.append("建议在非游玩时段执行大版本更新")
        return recs