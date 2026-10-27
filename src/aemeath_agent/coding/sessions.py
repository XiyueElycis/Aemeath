"""编程智能体 · 会话持久化。

一次任务跑完就把轨迹扔掉，等于每次都得从头解释上下文。这里把任务、轨迹与
结果存成一份会话文件，下次可以 ``--resume`` 接着做。

存储位置：``<应用数据目录>/coding/sessions/<id>.json``，按创建时间排序。
只保留最近 :data:`MAX_SESSIONS` 份，避免长期使用把磁盘磨满。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_SESSIONS = 50


def sessions_dir(directory: Optional[Path | str] = None) -> Path:
    if directory is not None:
        return Path(directory)
    base = os.environ.get("AEMEATH_AGENT_DATA_HOME") or os.path.expanduser(
        "~/.aemeath_agent")
    return Path(base) / "coding" / "sessions"


class SessionStore:
    """会话的读写。"""

    def __init__(self, directory: Optional[Path | str] = None) -> None:
        self.dir = sessions_dir(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    def new_id(self) -> str:
        return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    def save(self, *, task: str, root: str,
             messages: List[Dict[str, Any]],
             steps: List[Dict[str, Any]],
             finished: bool, final: str = "", error: str = "",
             session_id: Optional[str] = None) -> str:
        """写入一份会话，返回会话 id。"""
        sid = session_id or self.new_id()
        payload = {
            "id": sid,
            "task": task,
            "root": root,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "finished": finished,
            "final": final,
            "error": error,
            "steps": steps,
            "messages": messages,
        }
        target = self.dir / f"{sid}.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(target)
        self._trim()
        return sid

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        """读一份会话；不存在或损坏返回 None。"""
        path = self.dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        """会话摘要（最新在前）。"""
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.dir.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            rows.append({
                "id": str(data.get("id") or path.stem),
                "task": str(data.get("task") or ""),
                "root": str(data.get("root") or ""),
                "at": str(data.get("at") or ""),
                "finished": bool(data.get("finished")),
                "steps": len(data.get("steps") or []),
                "final": str(data.get("final") or ""),
            })
        return rows

    def _trim(self) -> None:
        files = sorted(self.dir.glob("*.json"))
        for old in files[:-MAX_SESSIONS]:
            try:
                old.unlink()
            except OSError:
                pass


__all__ = ["SessionStore", "sessions_dir", "MAX_SESSIONS"]
