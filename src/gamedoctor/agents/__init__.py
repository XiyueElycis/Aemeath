"""智能体子系统。

集成多种领域智能体（兼容性、安装、DLC、性能、网络等），统一由
:mod:`gamedoctor.orchestrator` 编排。
"""

from .base_agent import AgentResult, AgentTask, BaseAgent, MultiAgentSystem

__all__ = ["AgentResult", "AgentTask", "BaseAgent", "MultiAgentSystem"]