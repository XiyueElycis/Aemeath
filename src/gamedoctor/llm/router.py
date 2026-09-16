"""模型路由（PRD §4.6 F6）。

按推理步骤选择模型：分类/摘要用轻量模型，方案生成用强模型，自检用不同模型，
离线场景用 Ollama 本地模型兜底。
"""

from __future__ import annotations

from enum import Enum

from ..config import Config, load
from .client import HttpLLMClient, LLMClient


class Task(str, Enum):
    """推理步骤类型。"""

    CLASSIFY = "classify"     # 错误分类（轻量）
    SUMMARIZE = "summarize"   # 日志摘要（轻量）
    GENERATE = "generate"     # 方案生成（强模型）
    CRITIC = "critic"         # 交叉自检（与生成不同）


class ModelRouter:
    """按任务路由到对应模型。"""

    def __init__(self, config: Config | None = None, client: LLMClient | None = None):
        self.config = config or load()
        self.client = client or HttpLLMClient(self.config)

    def model_for(self, task: Task) -> str:
        # 分类 / 摘要用轻量模型（快且便宜），方案生成用强模型
        key = {
            Task.CLASSIFY: "llm.model_light",
            Task.SUMMARIZE: "llm.model_light",
            Task.GENERATE: "llm.model_strong",
            Task.CRITIC: "llm.model_critic",
        }[task]
        model = self.config.get(key, "")
        if task == Task.CRITIC and not model:
            # 自检模型留空则回退到强模型；理想情况应配置为与生成模型不同的模型，
            # 以实现真正的交叉审查（PRD §4.6 方案自检用不同模型）。
            model = self.config.get("llm.model_strong", "")
        return model

    def complete(self, task: Task, prompt: str) -> str:
        """按任务选定模型并完成一次补全。"""
        return self.client.complete(prompt, model=self.model_for(task))
