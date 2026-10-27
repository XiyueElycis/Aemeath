"""爱弥斯 · 心动模式（Galgame 聊天模式）。

好感度养成 + 章节推进 + 随机/模型决策事件（吃饭、约会、一起睡觉等）+
三种结局。引擎见 :class:`~aemeath_agent.galgame.engine.GalgameEngine`。
"""

from __future__ import annotations

from .engine import GalgameEngine
from .memory import GalgameMemory
from .state import (
    ENDING_FADE,
    ENDING_FRIEND,
    ENDING_LOVE,
    GalgameSave,
)
from .world import WorldContext, get_world

__all__ = [
    "GalgameEngine",
    "GalgameMemory",
    "GalgameSave",
    "WorldContext",
    "get_world",
    "ENDING_FADE",
    "ENDING_FRIEND",
    "ENDING_LOVE",
]
