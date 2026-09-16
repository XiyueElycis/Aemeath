"""游戏启动优化智能体。

优化启动性能：启动参数优化、后台进程管理、预加载策略、性能调优建议。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class LaunchOptimizationResult:
    """启动优化结果。"""

    game_name: str
    launch_args: List[str] = field(default_factory=list)
    background_processes: List[str] = field(default_factory=list)
    preload_suggestions: List[str] = field(default_factory=list)
    tuning_suggestions: List[str] = field(default_factory=list)


class LaunchOptimizerAgent(BaseAgent):
    """启动优化智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("launch_optimizer", config)
        self.capabilities = [
            "launch_optimizer", "optimization", "launch_params", "background_cleanup", "preloading",
        ]
        self.optimize_settings = self.config.get("optimize_settings", True)
        self.background_cleanup = self.config.get("background_cleanup", True)

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Launch Optimizer Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")
            context: GameContext = data.get("game_context")

            launch_args = self._build_launch_args(game_name, context)
            bg_processes = self._suggest_background_cleanup() if self.background_cleanup else []
            preload = self._preload_suggestions(game_name)
            tuning = self._tuning_suggestions(game_name) if self.optimize_settings else []

            result = LaunchOptimizationResult(
                game_name=game_name,
                launch_args=launch_args,
                background_processes=bg_processes,
                preload_suggestions=preload,
                tuning_suggestions=tuning,
            )

            self.record_metric("launch_args_count", len(launch_args))
            return AgentResult(
                success=True,
                data=result,
                message=f"Launch optimization completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Launch optimization failed", e)
            return AgentResult(
                success=False,
                message=f"Launch optimization failed: {e}",
                errors=[str(e)],
            )

    def _build_launch_args(self, game_name: str, context: GameContext) -> List[str]:
        """根据游戏特性生成启动参数（简化启发）。"""
        args: List[str] = []
        name = game_name.lower()
        if any(k in name for k in ("unreal", "dx12", "ray tracing")):
            args.append("-dx12")
        if "windowed" in name:
            args.append("-windowed")
        # 常见强制独占全屏 / 跳过启动器
        args.append("-novid" if "source" in name else "-nolauncher")
        return args

    def _suggest_background_cleanup(self) -> List[str]:
        """用 psutil 实际枚举高占用后台进程。

        取内存占用 Top 5 且非系统关键进程（忽略 System/空闲进程），
        给出可关闭项；psutil 不可用时回退到通用建议。
        """
        try:
            import psutil
        except ImportError:
            return ["浏览器多开标签页", "录屏/直播软件", "云同步客户端"]

        # 系统关键进程白名单：这些占用高也不建议结束
        _SYSTEM_CRITICAL = {"system", "system idle process", "registry", "memcompression"}
        candidates: List[tuple] = []
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if not name or name in _SYSTEM_CRITICAL:
                    continue
                mem = proc.memory_info().rss
            except Exception:  # noqa: BLE001 - 权限/进程消失等跳过
                continue
            # 300MB 以上才算值得清理的后台占用
            if mem >= 300 * 1024 * 1024:
                candidates.append((mem, proc.info.get("name")))

        if not candidates:
            return ["未发现明显高占用后台进程，当前环境良好"]
        candidates.sort(reverse=True)
        top = candidates[:5]
        return [
            f"{name}（占用约 {mem / 1024**3:.1f} GB 内存，可考虑关闭）"
            for mem, name in top
        ]

    def _preload_suggestions(self, game_name: str) -> List[str]:
        name = game_name.lower()
        if any(k in name for k in ("open world", "gta", "assassin")):
            return ["建议启用平台预载/预着色功能"]
        return ["可关闭启动画面动画以加快进入主菜单"]

    def _tuning_suggestions(self, game_name: str) -> List[str]:
        return ["将游戏所在磁盘设为优先读取", "检查显卡驱动是否为最新版本"]