"""爱弥斯 · 编程智能体。

一个真正的 ReAct 循环：模型自己决定下一步调哪个工具（读文件 / 搜索 /
改文件 / 执行命令），结果回灌后再决定继续或收尾。工作区锁在根目录内，
写文件与执行命令都要先过审批。

用法::

    python -m aemeath_agent.coding "把 parse_json 的空输入 bug 修掉" --root .
"""

from __future__ import annotations

from .agent import AgentResult, AgentStep, CodingAgent, run_task
from .checkpoints import CheckpointStore
from .codeindex import search_code
from .mcp_client import MCPError, StdioMCPClient
from .permissions import PermissionStore
from .repomap import build_repo_map
from .sessions import SessionStore
from .tools import ApprovalAsk, ToolOutcome, describe_tools, execute_tool
from .workspace import Workspace, WorkspaceError

__all__ = [
    "CodingAgent",
    "AgentResult",
    "AgentStep",
    "run_task",
    "Workspace",
    "WorkspaceError",
    "ApprovalAsk",
    "ToolOutcome",
    "describe_tools",
    "execute_tool",
    "build_repo_map",
    "search_code",
    "CheckpointStore",
    "PermissionStore",
    "SessionStore",
    "StdioMCPClient",
    "MCPError",
]
