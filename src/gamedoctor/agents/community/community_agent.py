"""社区资源聚合智能体。

整合社区资源：教程攻略聚合、视频推荐、社区讨论监控、UGC 管理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class CommunityResult:
    """社区资源聚合结果。"""

    game_name: str
    tutorials: List[Dict[str, str]] = field(default_factory=list)
    videos: List[Dict[str, str]] = field(default_factory=list)
    discussions: List[Dict[str, str]] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class CommunityAgent(BaseAgent):
    """社区资源聚合智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("community_agent", config)
        self.capabilities = [
            "community", "tutorial_aggregation", "video_recommendation",
            "discussion_monitoring",
        ]
        self.aggregation_level = self.config.get("aggregation_level", "high")
        self.language_filter = self.config.get("language_filter", "zh")

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Community Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        data = task.data or {}
        game_name = data.get("game_name", "Unknown")

        # 能力尚未接入：未对接任何社区/视频/论坛 API，返回空集合，
        # 不虚构教程、视频、讨论帖子等资源条目。
        result = CommunityResult(
            game_name=game_name,
            tutorials=[],
            videos=[],
            discussions=[],
            recommendations=[],
        )
        unverified = [
            "教程攻略聚合",
            "视频推荐",
            "社区讨论监控",
        ]
        self.record_metric("tutorial_count", 0)
        return AgentResult(
            success=True,
            data=result,
            message=(
                f"Community aggregation skipped for {game_name}：社区资源聚合能力尚未接入，"
                "未抓取任何外部资源，以下未验证项不可作为诊断结论"
            ),
            unverified=unverified,
        )

    def _aggregate_tutorials(self, game_name: str) -> List[Dict[str, str]]:
        # TODO: 接入社区/百科 API 聚合真实教程
        return []

    def _recommend_videos(self, game_name: str) -> List[Dict[str, str]]:
        # TODO: 接入视频平台搜索 API
        return []

    def _monitor_discussions(self, game_name: str) -> List[Dict[str, str]]:
        # TODO: 监控论坛/贴吧热帖
        return []

    def _build_recommendations(self, game_name: str) -> List[str]:
        # 无真实聚合结果时不产出「已聚合资源」「已按语言过滤」等假声明
        return []