"""游戏音频专家智能体。

音频问题诊断与优化：设备检测、格式兼容性、环绕声配置、问题诊断修复。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class AudioResult:
    """音频诊断与优化结果。"""

    game_name: str
    detected_devices: List[str] = field(default_factory=list)
    format_issues: List[str] = field(default_factory=list)
    spatial_audio_enabled: bool = False
    problems: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class AudioExpertAgent(BaseAgent):
    """音频专家智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("audio_expert", config)
        self.capabilities = [
            "audio", "device_detection", "format_compatibility", "spatial_audio",
        ]
        self.auto_optimization = self.config.get("auto_optimization", True)
        self.spatial_audio = self.config.get("spatial_audio", True)

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Audio Expert Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")

            devices = self._detect_devices()
            format_issues = self._check_format_compatibility()
            problems = self._diagnose_problems(devices)

            result = AudioResult(
                game_name=game_name,
                detected_devices=devices,
                format_issues=format_issues,
                spatial_audio_enabled=self.spatial_audio,
                problems=problems,
                recommendations=self._build_recommendations(problems, format_issues),
            )

            self.record_metric("device_count", len(devices))
            return AgentResult(
                success=True,
                data=result,
                message=f"Audio analysis completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Audio analysis failed", e)
            return AgentResult(
                success=False,
                message=f"Audio analysis failed: {e}",
                errors=[str(e)],
            )

    def _detect_devices(self) -> List[str]:
        # TODO: 接入音频子系统（如 Windows Core Audio / WASAPI）枚举设备
        return ["默认输出设备"]

    def _check_format_compatibility(self) -> List[str]:
        # TODO: 检查游戏所用音频格式（如 XAudio2 / FMOD / Wwise）与本机兼容性
        return ["部分老游戏的多声道格式在本机可能不被支持"]

    def _diagnose_problems(self, devices: List[str]) -> List[str]:
        problems: List[str] = []
        if not devices:
            problems.append("未检测到音频输出设备")
        return problems

    def _build_recommendations(self, problems: List[str], format_issues: List[str]) -> List[str]:
        recs: List[str] = []
        recs.extend(problems)
        recs.extend(format_issues)
        if self.spatial_audio:
            recs.append("已启用空间音频/环绕声优化")
        if not problems:
            recs.append("音频设备正常")
        return recs