"""游戏社交管理智能体。

社交功能集成：好友状态监控、语音聊天管理、社交账户集成、隐私设置管理。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class SocialResult:
    """社交管理结果。"""

    game_name: str
    friends_online: List[str] = field(default_factory=list)
    voice_channels: List[str] = field(default_factory=list)
    linked_accounts: List[str] = field(default_factory=list)
    privacy_settings: Dict[str, str] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


class SocialAgent(BaseAgent):
    """社交管理智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("social_agent", config)
        self.capabilities = ["social", "friend_status", "voice_chat", "privacy"]
        self.auto_friend_status = self.config.get("auto_friend_status", True)

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Social Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")

            result = SocialResult(
                game_name=game_name,
                friends_online=self._monitor_friends(game_name),
                voice_channels=self._list_voice_channels(game_name),
                linked_accounts=self._list_linked_accounts(game_name),
                privacy_settings=self._privacy_settings(),
                recommendations=self._build_recommendations(game_name),
            )

            self.record_metric("online_friend_count", len(result.friends_online))
            return AgentResult(
                success=True,
                data=result,
                message=f"Social management completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Social management failed", e)
            return AgentResult(
                success=False,
                message=f"Social management failed: {e}",
                errors=[str(e)],
            )

    def _monitor_friends(self, game_name: str) -> List[str]:
        # TODO: 接入平台好友 API（Steam Friends 等）
        return []

    def _list_voice_channels(self, game_name: str) -> List[str]:
        return ["游戏内语音", "Discord"]

    def _list_linked_accounts(self, game_name: str) -> List[str]:
        # TODO: 从本地配置读取已绑定平台
        return ["steam"]

    def _privacy_settings(self) -> Dict[str, str]:
        return {
            "profile_visibility": "friends_only",
            "online_status": "private",
            "game_activity": "friends_only",
        }

    def _build_recommendations(self, game_name: str) -> List[str]:
        recs = []
        if self.auto_friend_status:
            recs.append("已开启好友状态自动监控")
        recs.append("建议将个人资料可见性设为仅好友")
        return recs