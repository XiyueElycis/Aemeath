"""游戏音频专家智能体。

音频问题诊断与优化：设备检测、格式兼容性、环绕声配置、问题诊断修复。
"""

from __future__ import annotations

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
        data = task.data or {}
        game_name = data.get("game_name", "Unknown")

        # 能力尚未接入：不做任何设备/格式探测，返回空集合而非虚构检测结果。
        # 真实枚举将在接入 Windows Core Audio / WASAPI 后于下方各私有方法实现。
        result = AudioResult(
            game_name=game_name,
            detected_devices=[],
            format_issues=[],
            spatial_audio_enabled=False,
            problems=[],
            recommendations=[],
        )
        unverified = [
            "音频输出设备枚举",
            "音频格式兼容性检测",
            "空间音频/环绕声配置检测",
        ]
        self.record_metric("device_count", 0)
        return AgentResult(
            success=True,
            data=result,
            message=(
                f"Audio analysis skipped for {game_name}：音频检测能力尚未接入，"
                "未对设备与格式做任何实测，以下未验证项不可作为诊断结论"
            ),
            unverified=unverified,
        )

    def _detect_devices(self) -> List[str]:
        # TODO: 接入音频子系统（如 Windows Core Audio / WASAPI）枚举设备
        return []

    def _check_format_compatibility(self) -> List[str]:
        # TODO: 检查游戏所用音频格式（如 XAudio2 / FMOD / Wwise）与本机兼容性
        return []

    def _diagnose_problems(self, devices: List[str]) -> List[str]:
        problems: List[str] = []
        if not devices:
            problems.append("未检测到音频输出设备")
        return problems

    def _build_recommendations(self, problems: List[str], format_issues: List[str]) -> List[str]:
        # 能力未接入：不得生成「设备正常」「已优化」之类无实测依据的建议
        recs: List[str] = []
        recs.extend(problems)
        recs.extend(format_issues)
        return recs