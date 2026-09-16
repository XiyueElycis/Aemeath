"""已安装技能（Skill）目录。

集中维护本应用内置的技能元数据（name / 中文名 / 分类 / 说明），
供后端 ``GET /skills`` 接口与桌面端「技能管理」页面共用同一份清单。
启用状态由 :mod:`gamedoctor.settings` 持久化（默认全部启用）。
"""

from __future__ import annotations

from typing import Any, Dict, List

# name / display_name / category / description
SKILL_CATALOG: List[Dict[str, str]] = [
    {"name": "file_ops", "display_name": "文件读写", "category": "系统",
     "description": "游戏目录内文件的读写、备份与安全替换"},
    {"name": "registry_edit", "display_name": "注册表编辑", "category": "系统",
     "description": "读写游戏相关的 Windows 注册表键值"},
    {"name": "process_manage", "display_name": "进程管理", "category": "系统",
     "description": "启动、结束游戏进程与相关后台服务"},
    {"name": "config_parse", "display_name": "配置解析", "category": "解析",
     "description": "解析游戏 INI / JSON / XML 配置文件"},
    {"name": "game_locate", "display_name": "游戏定位", "category": "识别",
     "description": "识别并定位已安装游戏及其存档、配置路径"},
    {"name": "save_backup", "display_name": "存档备份", "category": "存档",
     "description": "游戏存档的自动备份、还原与版本管理"},
    {"name": "launcher_probe", "display_name": "启动器探查", "category": "识别",
     "description": "识别 Steam / Epic 等启动器及对应游戏库"},
    {"name": "net_probe", "display_name": "网络诊断", "category": "网络",
     "description": "诊断游戏联网、加速与服务器连通性"},
]


def list_skills() -> List[Dict[str, str]]:
    """返回技能清单副本（不含启用状态，启用状态由调用方合并）。"""
    return [dict(item) for item in SKILL_CATALOG]


def skill_names() -> List[str]:
    """返回全部技能 name。"""
    return [item["name"] for item in SKILL_CATALOG]