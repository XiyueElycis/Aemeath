"""社区资源聚合智能体。

整合社区资源：教程攻略聚合、视频推荐、社区讨论监控、UGC 管理。
"""

from __future__ import annotations

import logging
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
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")

            result = CommunityResult(
                game_name=game_name,
                tutorials=self._aggregate_tutorials(game_name),
                videos=self._recommend_videos(game_name),
                discussions=self._monitor_discussions(game_name),
                recommendations=self._build_recommendations(game_name),
            )

            self.record_metric("tutorial_count", len(result.tutorials))
            return AgentResult(
                success=True,
                data=result,
                message=f"Community aggregation completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Community aggregation failed", e)
            return AgentResult(
                success=False,
                message=f"Community aggregation failed: {e}",
                errors=[str(e)],
            )

    def _aggregate_tutorials(self, game_name: str) -> List[Dict[str, str]]:
        # TODO: 接入社区/百科 API 聚合真实教程
        return [{"title": f"{game_name} 新手入门", "source": "community", "type": "攻略"}]

    def _recommend_videos(self, game_name: str) -> List[Dict[str, str]]:
        # TODO: 接入视频平台搜索 API
        return [{"title": f"{game_name} 通关流程", "source": "video", "type": "视频"}]

    def _monitor_discussions(self, game_name: str) -> List[Dict[str, str]]:
        # TODO: 监控论坛/贴吧热帖
        return [{"title": f"{game_name} 常见问题讨论", "source": "forum", "type": "讨论"}]

    def _build_recommendations(self, game_name: str) -> List[str]:
        recs = [f"已聚合 {self.aggregation_level} 级别社区资源"]
        if self.language_filter:
            recs.append(f"已按语言过滤：{self.language_filter}")
        return recs