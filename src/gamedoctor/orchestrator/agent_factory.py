"""智能体工厂。

负责创建和管理所有智能体实例。
"""

from __future__ import annotations

import importlib
import logging
from typing import Dict, List, Type, Any

from ..agents.base_agent import BaseAgent
from .orchestrator import GameAgentOrchestrator


class AgentFactory:
    """智能体工厂。"""

    _agents: Dict[str, Type[BaseAgent]] = {}

    @classmethod
    def register_agent(cls, name: str, agent_class: Type[BaseAgent]) -> None:
        """注册智能体类。"""
        cls._agents[name] = agent_class
        logging.getLogger("gamedoctor.agent_factory").info(f"Registered agent: {name}")

    @classmethod
    def create_agent(cls, name: str, config: Dict[str, Any] = None) -> BaseAgent:
        """创建智能体实例。"""
        if name not in cls._agents:
            raise ValueError(f"Unknown agent: {name}")

        agent_class = cls._agents[name]
        return agent_class(config)

    @classmethod
    def get_available_agents(cls) -> List[str]:
        """获取可用的智能体列表。"""
        return list(cls._agents.keys())

    @classmethod
    def get_agent_info(cls, name: str) -> Dict[str, Any]:
        """获取智能体信息。"""
        if name not in cls._agents:
            raise ValueError(f"Unknown agent: {name}")

        agent_class = cls._agents[name]
        return {
            "name": name,
            "class": agent_class.__name__,
            "module": agent_class.__module__,
            "capabilities": getattr(agent_class, "capabilities", [])
        }


class AgentManager:
    """智能体管理器。"""

    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self._display_names: Dict[str, str] = {}
        self.orchestrator = GameAgentOrchestrator()
        self.logger = logging.getLogger("gamedoctor.agent_manager")

    def register_agents(self, agent_configs: List[Dict[str, Any]]) -> None:
        """批量注册智能体。"""
        for config in agent_configs:
            name = config["name"]
            agent_type = config["type"]
            agent_config = config.get("config", {})
            display_name = config.get("display_name", name)

            try:
                agent = AgentFactory.create_agent(agent_type, agent_config)
                self.agents[name] = agent
                self._display_names[name] = display_name
                self.logger.info(f"Registered agent: {name}")
            except Exception as e:
                self.logger.error(f"Failed to register agent {name}: {e}")

    def setup_orchestrator(self) -> None:
        """设置编排器。"""
        self.orchestrator.register_agents(list(self.agents.values()))

    def get_agent(self, name: str) -> BaseAgent:
        """获取智能体实例。"""
        return self.agents[name]

    def get_display_name(self, name: str) -> str:
        """智能体中文展示名，未知则回退到 name。"""
        return self._display_names.get(name, name)

    def list_agent_names(self) -> List[str]:
        """按注册顺序返回所有智能体名。"""
        return list(self.agents.keys())

    def list_agents(self) -> List[Dict[str, Any]]:
        """列出所有已注册的智能体（含中文名）。"""
        result = []
        for name, agent in self.agents.items():
            result.append({
                "name": name,
                "display_name": self._display_names.get(name, name),
                "capabilities": agent.get_capabilities(),
                "status": "initialized"
            })
        return result


# 预定义的智能体配置
DEFAULT_AGENT_CONFIGS = [
    {
        "name": "compatibility",
        "display_name": "兼容性检测",
        "type": "compatibility",
        "config": {"auto_check": True}
    },
    {
        "name": "installation",
        "display_name": "安装规划",
        "type": "installation",
        "config": {"preferred_source": "steam", "auto_verify": True}
    },
    {
        "name": "game_content",
        "display_name": "游戏内容管理",
        "type": "game_content",
        # 合并原 update_manager / save_manager / dlc_manager（版本强相关三件套）
        "config": {
            "auto_update": True, "beta_opt_in": False,
            "auto_backup": True, "cloud_sync": True,
            "conflict_detection": True,
        }
    },
    {
        "name": "launch_optimizer",
        "display_name": "启动优化",
        "type": "launch_optimizer",
        "config": {"optimize_settings": True, "background_cleanup": True}
    },
    {
        "name": "community_agent",
        "display_name": "社区资源聚合",
        "type": "community",
        "config": {"aggregation_level": "high", "language_filter": "zh"}
    },
    {
        "name": "social_agent",
        "display_name": "社交管理",
        "type": "social",
        "config": {"auto_friend_status": True}
    },
    {
        "name": "perf_security",
        "display_name": "性能安全",
        "type": "perf_security",
        # 合并原 performance_analyst + security_agent（运行体检一家亲）
        "config": {
            "detailed_analysis": True, "optimization_suggestions": True,
            "anti_cheat_check": True, "scan_frequency": "daily",
        }
    },
    {
        "name": "audio_expert",
        "display_name": "音频专家",
        "type": "audio",
        "config": {"auto_optimization": True, "spatial_audio": True}
    },
    {
        "name": "network_expert",
        "display_name": "网络专家",
        "type": "network",
        "config": {"route_optimization": True, "server_recommendation": True}
    },
    {
        "name": "script_editor",
        "display_name": "脚本编辑",
        "type": "script_editor",
        "config": {"allow_absolute": True}
    },
    {
        "name": "web_search",
        "display_name": "联网搜索",
        "type": "web_search",
        "config": {"max_hits": 5, "timeout": 15, "allow_absolute": True}
    },
    {
        "name": "log_analyzer",
        "display_name": "日志分析",
        "type": "log_analyzer",
        "config": {}
    }
]


# 智能体类型 → (相对模块路径, 类名)。与 DEFAULT_AGENT_CONFIGS 中的 "type" 一致。
_AGENT_REGISTRY: List[tuple[str, str, str]] = [
    ("compatibility", "..agents.compatibility.compatibility_agent", "CompatibilityAgent"),
    ("installation", "..agents.installation.installation_agent", "InstallationAgent"),
    ("game_content", "..agents.content.game_content_agent", "GameContentAgent"),
    ("launch_optimizer", "..agents.optimization.launch_optimizer_agent", "LaunchOptimizerAgent"),
    ("community", "..agents.community.community_agent", "CommunityAgent"),
    ("social", "..agents.social.social_agent", "SocialAgent"),
    ("perf_security", "..agents.health.perf_security_agent", "PerfSecurityAgent"),
    ("audio", "..agents.audio.audio_expert_agent", "AudioExpertAgent"),
    ("network", "..agents.network.network_expert_agent", "NetworkExpertAgent"),
    ("script_editor", "..agents.scripts.script_editor_agent", "ScriptEditorAgent"),
    ("web_search", "..agents.web.web_search_agent", "WebSearchAgent"),
    ("log_analyzer", "..agents.logs.log_analyzer_agent", "LogAnalyzerAgent"),
]


# 自动注册所有智能体
def register_all_agents() -> None:
    """注册所有智能体类型。

    按 :data:`_AGENT_REGISTRY` 逐个导入并注册；单一类型导入失败不影响其余注册，
    缺失的可选依赖（如某些会拖入重依赖的智能体）因此不会阻断整体启动。
    """
    logger = logging.getLogger("gamedoctor.agent_factory")
    for agent_type, module_path, class_name in _AGENT_REGISTRY:
        try:
            module = importlib.import_module(module_path, package=__package__)
            AgentFactory.register_agent(agent_type, getattr(module, class_name))
        except Exception:  # noqa: BLE001
            logger.warning("Failed to register agent '%s'", agent_type, exc_info=True)


# 在模块加载时自动注册
register_all_agents()