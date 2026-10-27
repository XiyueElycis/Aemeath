"""心动模式 · 长期记忆桥接。

两层记忆：

1. **用户画像（只读共享）**：主控制台陪伴模式总结出的 ``user_profile.json``，
   让爱弥斯记得「你这个人」；只读注入，**绝不回写**——否则角色扮演内容会污染
   工具型画像，且画像只增不减，污染不可逆。
2. **心动模式专属记忆（读写独立）**：独立目录
   ``~/.aemeath_agent/galgame/memory/``，与主控制台完全隔离地记录心动模式的对话
   与其自身的画像总结。

所有方法都是旁路容错：记忆只是锦上添花，任何异常都不得影响主对话。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..memory import MemoryManager
from .state import memory_dir


class GalgameMemory:
    """心动模式记忆门面（独立读写 + 只读共享画像）。"""

    def __init__(self, directory: Optional[Path | str] = None,
                 shared_directory: Optional[Path | str] = None) -> None:
        # 心动模式自己的滚动记录 / 画像（读写）
        self.mm = MemoryManager(directory or memory_dir())
        # 主控制台画像（只读）；默认落回其标准目录
        self._shared = MemoryManager(shared_directory)

    # ------------------------------------------------------------------ #
    def profile_block(self) -> str:
        """主控制台画像（只读）+ 心动模式自己的画像；任一段失败都跳过。"""
        blocks = []
        for mm in (self._shared, self.mm):
            if mm is None:
                continue
            try:
                block = mm.profile_block()
            except Exception:  # noqa: BLE001 — 只读旁路
                block = ""
            if block:
                blocks.append(block)
        return "\n".join(blocks)

    def record(self, user_text: str, assistant_text: str) -> int:
        """记录一轮心动模式对话，返回累计 token；失败返回 0。"""
        try:
            return self.mm.record(user_text, assistant_text)
        except Exception:  # noqa: BLE001 — 旁路容错
            return 0

    def threshold_reached(self, threshold: int) -> bool:
        try:
            return self.mm.threshold_reached(threshold)
        except Exception:  # noqa: BLE001
            return False

    def summarize(self, client: object, model: Optional[str] = None) -> None:
        """触发画像总结（旁路容错，异常吞掉）。"""
        try:
            self.mm.summarize(client, model)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["GalgameMemory"]