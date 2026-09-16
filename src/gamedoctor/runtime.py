"""智能体运行状态追踪（供「操作过程可视化」）。

维护一个进程级单例 :class:`OperationTracker`，记录：

- ``current`` —— 当前正在运行的智能体、阶段、访问路径
- ``events``  —— 最近若干条操作事件（访问目录 / 修改内容 / 阶段切换等）

后端在智能体执行前后写入，前端通过 ``GET /status`` 轮询展示。
线程安全：所有写入均加锁，适配 FastAPI 异步并发场景。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional


class OperationTracker:
    """线程安全的运行状态追踪器。"""

    def __init__(self, max_events: int = 200) -> None:
        self._lock = threading.Lock()
        self._max_events = max_events
        self._events: List[Dict[str, Any]] = []
        self._current: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ #
    def set_current(self, agent: str, display_name: str, phase: str, path: str = "") -> None:
        """标记当前正在运行的智能体。"""
        with self._lock:
            self._current = {
                "agent": agent,
                "display_name": display_name,
                "phase": phase,
                "path": path,
                "started_at": time.time(),
            }

    def clear_current(self) -> None:
        with self._lock:
            self._current = None

    def log(self, kind: str, message: str, path: str = "") -> None:
        """追加一条操作事件。kind 可取 access/modify/phase/info。"""
        with self._lock:
            self._events.append({
                "ts": time.time(),
                "kind": kind,
                "message": message,
                "path": path,
            })
            # 只保留最近 max_events 条
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

    def snapshot(self) -> Dict[str, Any]:
        """返回当前状态 + 最近事件（倒序，最新在前）。"""
        with self._lock:
            events = list(reversed(self._events))
            return {
                "current": dict(self._current) if self._current else None,
                "events": events,
            }


# 进程级单例
_tracker = OperationTracker()


def get_tracker() -> OperationTracker:
    """获取全局运行状态追踪器。"""
    return _tracker