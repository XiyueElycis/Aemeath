"""编程智能体 · 权限记忆（always-allow 规则）。

每次审批都问一遍，用户很快会开始盲按 y——那比不审批更危险。所以提供
「以后这类操作别再问」的能力：

- 命令行里回答 ``a``（always）时，把这次的操作类型记住；
- 写文件类按**工具名**记住（``write_file`` / ``delete_path`` …）；
- 命令类按**命令前缀**记住（``python -m pytest`` 不会放行 ``python -m pip``，
  更不会放行 ``rm -rf``）。

规则存在应用数据目录，按工作区根路径分桶——换个项目不会自动继承上一条的
放行规则。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# 命令前缀最多取几个词：太短等于全放行，太长则规则永远匹配不上
COMMAND_PREFIX_TOKENS = 3


def _data_home() -> Path:
    base = os.environ.get("AEMEATH_AGENT_DATA_HOME") or os.path.expanduser(
        "~/.aemeath_agent")
    return Path(base)


def permissions_path(root: Path, directory: Optional[Path | str] = None) -> Path:
    if directory is not None:
        return Path(directory) / "permissions.json"
    key = hashlib.md5(str(Path(root).resolve()).lower().encode("utf-8")).hexdigest()[:12]
    return _data_home() / "coding" / "permissions" / f"{key}.json"


def command_prefix(command: str) -> str:
    """取命令的前几个词作为放行前缀。"""
    tokens = (command or "").split()
    return " ".join(tokens[:COMMAND_PREFIX_TOKENS])


class PermissionStore:
    """一个工作区的放行规则。"""

    def __init__(self, root: Path | str = ".",
                 path: Optional[Path | str] = None) -> None:
        self.root = Path(root).resolve()
        self.path = permissions_path(self.root, path)
        self._rules: Dict[str, List[str]] = {"tools": [], "commands": []}
        self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._rules["tools"] = [str(x) for x in data.get("tools", [])]
            self._rules["commands"] = [str(x) for x in data.get("commands", [])]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._rules, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ------------------------------------------------------------------ #
    def is_allowed(self, ask: Any) -> bool:
        """这次操作是否已被长期放行。"""
        if getattr(ask, "kind", "") == "shell":
            command = str(getattr(ask, "target", "") or "")
            prefix = command_prefix(command)
            return any(prefix.startswith(rule) for rule in self._rules["commands"])
        tool = str(getattr(ask, "tool", "") or "")
        return bool(tool) and tool in self._rules["tools"]

    def remember(self, ask: Any) -> None:
        """把这次操作登记为「以后别再问」。"""
        if getattr(ask, "kind", "") == "shell":
            prefix = command_prefix(str(getattr(ask, "target", "") or ""))
            if prefix and prefix not in self._rules["commands"]:
                self._rules["commands"].append(prefix)
            self._save()
            return
        tool = str(getattr(ask, "tool", "") or "")
        if tool and tool not in self._rules["tools"]:
            self._rules["tools"].append(tool)
            self._save()

    def reset(self) -> None:
        """清空所有放行规则（用户反悔时用）。"""
        self._rules = {"tools": [], "commands": []}
        self._save()

    def snapshot(self) -> Dict[str, List[str]]:
        return {"tools": list(self._rules["tools"]),
                "commands": list(self._rules["commands"])}


__all__ = ["PermissionStore", "permissions_path", "command_prefix",
           "COMMAND_PREFIX_TOKENS"]
