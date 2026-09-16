"""LLM 模型目录（供 GUI 设置面板与对话服务使用）。

集中维护 provider →（api_base、默认模型、可选模型列表），让前端设置面板
与后端对话服务共用同一份元数据，避免各自硬编码。
"""

from __future__ import annotations

from typing import Any, Dict, List

# provider id → 元数据。字段说明：
#   name          前端展示名
#   api_base      兼容 OpenAI 的 chat/completions 端点前缀
#   default_model 该 provider 未手动选模型时的默认模型
#   models        可选模型下拉列表（用户也可自由填写）
LLM_CATALOG: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "name": "DeepSeek（深度求索）",
        "api_base": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro"],
    },
    "zhipu": {
        "name": "智谱 GLM",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
        "models": ["glm-4-plus", "glm-4-air", "glm-4.5", "glm-4.5-air", "glm-4.5-flash", "glm-5.3"],
    },
    "qwen": {
        "name": "通义千问（阿里）",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
    },
    "moonshot": {
        "name": "月之暗面 Kimi",
        "api_base": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-latest"],
    },
    "doubao": {
        "name": "火山方舟 豆包（字节）",
        "api_base": "https://ark.cn-beat.volces.com/api/v3",
        "default_model": "doubao-pro-32k",
        "models": ["doubao-pro-32k", "doubao-pro-128k", "doubao-lite-32k"],
    },
    "minimax": {
        "name": "MiniMax 海螺",
        "api_base": "https://api.minimax.chat/v1",
        "default_model": "abab6.5s-chat",
        "models": ["abab6.5s-chat", "abab6.5g-chat"],
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow",
        "api_base": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "THUDM/glm-4-9b-chat"],
    },
    "ollama": {
        "name": "Ollama 本地",
        "api_base": "http://localhost:11434/v1",
        "default_model": "llama3",
        "models": ["llama3", "qwen2.5", "deepseek-r1"],
    },
}


def providers() -> List[Dict[str, Any]]:
    """返回供前端渲染的 provider 列表。"""
    return [
        {"id": pid, "name": meta["name"], "api_base": meta["api_base"],
         "default_model": meta["default_model"], "models": meta["models"]}
        for pid, meta in LLM_CATALOG.items()
    ]


def default_api_base(provider: str) -> str:
    """provider 的默认 api_base，未知 provider 返回空串。"""
    return LLM_CATALOG.get(provider, {}).get("api_base", "")


def default_model(provider: str) -> str:
    """provider 的默认模型，未知 provider 返回空串。"""
    return LLM_CATALOG.get(provider, {}).get("default_model", "")