"""编程智能体 · MCP 服务器注册与调用。

配置存在 ``agent_settings.json`` 的 ``mcp_servers`` 段（与内置 ``mcps`` 启用
开关分开：那个是 UI 清单，这里才是真正要启动的进程）。

连接按服务器名缓存：启动一个 MCP 服务器（尤其 npx 拉起的）通常要几秒，
每次工具调用都重启会让任务慢到不可用；进程随本进程退出而结束，
另外提供 :func:`close_all` 供测试与退出时清理。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .mcp_client import MCPError, StdioMCPClient

# 已建立的连接：server 名 → 客户端
_clients: Dict[str, StdioMCPClient] = {}


def configured_servers(settings: Any) -> List[Dict[str, Any]]:
    """读取已注册且启用的服务器配置。"""
    try:
        rows = settings.get_mcp_servers() or []
    except Exception:  # noqa: BLE001 — 配置只读旁路
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        command = row.get("command")
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not command:
            continue
        if row.get("enabled", True) is False:
            continue
        out.append({
            "name": str(row.get("name") or command[0]),
            "command": [str(c) for c in command],
            "cwd": row.get("cwd") or None,
            "timeout": float(row.get("timeout") or 30.0),
        })
    return out


def _client_for(server: Dict[str, Any]) -> StdioMCPClient:
    """取（或建立）某个服务器的连接。"""
    name = server["name"]
    client = _clients.get(name)
    if client is not None:
        return client
    client = StdioMCPClient(server["command"], cwd=server["cwd"],
                            timeout=server["timeout"])
    client.start()
    _clients[name] = client
    return client


def list_tools(settings: Any, server: str = "") -> str:
    """列出服务器的工具；不给名字就汇总所有已注册服务器。"""
    servers = configured_servers(settings)
    if not servers:
        return ("（还没有注册任何 MCP 服务器。"
                "可在设置里添加 {name, command}，或用 --mcp-add 命令行添加）")
    picked = [s for s in servers if not server or s["name"] == server]
    if not picked:
        return f"（没有名为「{server}」的服务器）"
    lines: List[str] = []
    for item in picked:
        try:
            tools = _client_for(item).list_tools()
        except MCPError as exc:
            lines.append(f"{item['name']}：启动失败（{exc}）")
            continue
        names = "、".join(t.get("name", "") for t in tools) or "（无工具）"
        lines.append(f"{item['name']}（{len(tools)} 个）：{names}")
    return "\n".join(lines)


def call_tool(settings: Any, server: str, tool: str,
              arguments: Optional[Dict[str, Any]] = None) -> str:
    """调用某个服务器上的工具，返回文本结果。"""
    server = (server or "").strip()
    tool = (tool or "").strip()
    if not server or not tool:
        raise MCPError("缺少参数 server 或 tool")
    matching = [s for s in configured_servers(settings) if s["name"] == server]
    if not matching:
        raise MCPError(f"没有名为「{server}」的 MCP 服务器（先在设置里注册）")
    return _client_for(matching[0]).call_tool(tool, arguments or {})


def close_all() -> None:
    """关闭所有连接（测试隔离 / 进程退出）。"""
    for client in list(_clients.values()):
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    _clients.clear()


__all__ = ["configured_servers", "list_tools", "call_tool", "close_all"]
