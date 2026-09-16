"""已安装 MCP（Model Context Protocol）服务目录。

集中维护本应用内置 / 已连接的 MCP 服务元数据（name / 中文名 / 传输方式 / 说明），
供后端 ``GET /mcps`` 接口与桌面端「MCP 管理」页面共用同一份清单。
启用状态由 :mod:`gamedoctor.settings` 持久化（默认全部启用）。
"""

from __future__ import annotations

from typing import Dict, List

# name / display_name / transport / description
MCP_CATALOG: List[Dict[str, str]] = [
    {"name": "local_filesystem", "display_name": "本地文件系统", "transport": "stdio",
     "description": "游戏目录树的安全读写与静态分析"},
    {"name": "github", "display_name": "GitHub 仓库", "transport": "http",
     "description": "查询开源补丁、安装脚本与社区仓库"},
    {"name": "webbrowser", "display_name": "浏览器自动化", "transport": "http",
     "description": "驱动外部浏览器下载资源、查询方案"},
    {"name": "knowledge_db", "display_name": "知识库检索", "transport": "stdio",
     "description": "游戏兼容性知识库（SQLite + 向量检索）"},
    {"name": "lark", "display_name": "飞书集成", "transport": "http",
     "description": "飞书文档、多维表格与消息通知"},
]


def list_mcps() -> List[Dict[str, str]]:
    """返回 MCP 清单副本（不含启用状态，启用状态由调用方合并）。"""
    return [dict(item) for item in MCP_CATALOG]


def mcp_names() -> List[str]:
    """返回全部 MCP name。"""
    return [item["name"] for item in MCP_CATALOG]