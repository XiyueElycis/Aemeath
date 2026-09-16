"""游戏性能分析智能体。

深度性能分析：帧率监控、系统资源追踪、瓶颈识别、优化建议生成。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

import psutil

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext
from ...utils.system_info import get_system_info


@dataclass
class PerformanceAnalysisResult:
    """性能分析结果。"""

    game_name: str
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    gpu_info: List[Dict[str, Any]] = field(default_factory=list)
    bottlenecks: List[str] = field(default_factory=list)
    optimization_suggestions: List[str] = field(default_factory=list)


class PerformanceAnalystAgent(BaseAgent):
    """性能分析智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("performance_analyst", config)
        self.capabilities = [
            "performance", "fps_monitoring", "resource_tracking", "bottleneck_analysis",
        ]
        self.detailed_analysis = self.config.get("detailed_analysis", True)
        self.optimization_suggestions = self.config.get("optimization_suggestions", True)

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Performance Analyst Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")

            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory().percent
            system_info = get_system_info()
            gpus = system_info.get("gpu", [])

            bottlenecks = self._identify_bottlenecks(cpu, mem, gpus)
            suggestions = self._generate_suggestions(bottlenecks) if self.optimization_suggestions else []

            result = PerformanceAnalysisResult(
                game_name=game_name,
                cpu_usage=cpu,
                memory_usage=mem,
                gpu_info=gpus,
                bottlenecks=bottlenecks,
                optimization_suggestions=suggestions,
            )

            self.record_metric("cpu_usage", cpu)
            self.record_metric("memory_usage", mem)
            return AgentResult(
                success=True,
                data=result,
                message=f"Performance analysis completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Performance analysis failed", e)
            return AgentResult(
                success=False,
                message=f"Performance analysis failed: {e}",
                errors=[str(e)],
            )

    def _identify_bottlenecks(
        self, cpu: float, mem: float, gpus: List[Dict[str, Any]]
    ) -> List[str]:
        bottlenecks: List[str] = []
        if cpu > 85:
            bottlenecks.append("CPU 占用过高，可能存在 CPU 瓶颈")
        if mem > 90:
            bottlenecks.append("内存接近耗尽，建议增加物理内存或关闭后台程序")
        for gpu in gpus:
            vram = gpu.get("memory", 0)
            if vram and vram < 4 * 1024 * 1024 * 1024:  # 小于 4GB
                bottlenecks.append("显存偏小，高画质下可能出现显存瓶颈")
        if not bottlenecks:
            bottlenecks.append("未发现明显瓶颈")
        return bottlenecks

    def _generate_suggestions(self, bottlenecks: List[str]) -> List[str]:
        suggestions: List[str] = []
        for b in bottlenecks:
            if "CPU" in b:
                suggestions.append("降低游戏内物理/阴影等 CPU 密集项")
            if "内存" in b:
                suggestions.append("关闭多余后台进程，或升级内存")
            if "显存" in b:
                suggestions.append("降低纹理质量与分辨率缩放")
        return suggestions or ["当前性能良好，可保持现有设置"]