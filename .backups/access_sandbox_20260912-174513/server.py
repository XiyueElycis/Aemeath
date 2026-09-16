"""Game Doctor 智能体 HTTP 后端（FastAPI）。

暴露 REST API，供 C# WPF 桌面客户端调用：

- ``GET  /``             健康检查 + 版本
- ``GET  /agents``       智能体列表（含中文名 / 启用状态）
- ``GET  /status``       运行状态（当前智能体 / 访问目录 / 操作事件）
- ``GET  /settings``     设置（LLM 配置 + provider 目录 + 智能体启用）
- ``POST /settings/llm``   更新 LLM 配置
- ``POST /settings/agents`` 更新智能体启用开关
- ``POST /chat``         与大模型对话（LLM 自主决策调用智能体）
- ``POST /run``          手动执行单个智能体
- ``POST /analyze``      执行多个 / 全部智能体

启动：``gamedoctor-server`` 或 ``uvicorn gamedoctor.server:app``。
默认监听 ``127.0.0.1:8765``（可用环境变量 ``GAMEDOCTOR_HOST`` / ``GAMEDOCTOR_PORT`` 覆盖）。
"""

from __future__ import annotations

import dataclasses
import logging
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .agents.base_agent import AgentResult, AgentTask
from .llm.agent_router import AgentChatService
from .llm.catalog import providers as catalog_providers
from .mcps import MCP_CATALOG
from .orchestrator.agent_factory import AgentManager, DEFAULT_AGENT_CONFIGS
from .runtime import get_tracker
from .settings import Settings, get_settings
from .skills import SKILL_CATALOG

# --------------------------------------------------------------------------- #
# 全局单例：复用同一套智能体 + 编排器 + 设置 + 对话服务
# --------------------------------------------------------------------------- #
_manager: Optional[AgentManager] = None
_chat_service: Optional[AgentChatService] = None


def get_manager() -> AgentManager:
    """惰性初始化智能体管理器（注册 12 个智能体并接入编排器）。"""
    global _manager
    if _manager is None:
        _manager = AgentManager()
        _manager.register_agents(DEFAULT_AGENT_CONFIGS)
        _manager.setup_orchestrator()
    return _manager


def get_chat_service() -> AgentChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = AgentChatService(get_manager(), get_settings())
    return _chat_service


# --------------------------------------------------------------------------- #
# 请求模型
# --------------------------------------------------------------------------- #
class RunRequest(BaseModel):
    """手动执行单个智能体。"""
    game_name: str = ""
    game_dir: str = ""
    agent: str  # 智能体 name，如 "compatibility"


class AnalyzeRequest(BaseModel):
    """执行多个 / 全部智能体。"""
    game_name: str = ""
    game_dir: str = ""
    agents: Optional[List[str]] = None  # None 表示全部


class ChatRequest(BaseModel):
    """与大模型对话。"""
    message: str
    game_dir: str = ""
    history: Optional[List[Dict[str, str]]] = None
    enable_search: bool = False  # 是否允许大模型联网搜索/下载
    think_level: str = "medium"  # 思考强度 light/medium/heavy
    mode: str = "edit"  # 运行模式 edit（编辑）/ plan（方案，只读调查）


class LlmSettingsRequest(BaseModel):
    provider: str
    model: str = ""
    api_base: str = ""
    api_key: str = ""  # 空串表示不更新已有密钥


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class AgentsSettingsRequest(BaseModel):
    agents: Dict[str, bool]  # name -> enabled


class ItemsSettingsRequest(BaseModel):
    items: Dict[str, bool]  # name -> enabled（Skill / MCP 通用）


# --------------------------------------------------------------------------- #
# 序列化工具
# --------------------------------------------------------------------------- #
def _to_jsonable(obj: Any) -> Any:
    """把 dataclass / Path / 嵌套结构递归转成 JSON 安全类型。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _serialize_result(agent_name: str, result: AgentResult) -> Dict[str, Any]:
    return {
        "agent": agent_name,
        "success": result.success,
        "message": result.message,
        "data": _to_jsonable(result.data),
        "warnings": result.warnings or [],
        "errors": result.errors or [],
    }


def _agent_info(name: str, manager: AgentManager, settings: Settings) -> Dict[str, Any]:
    """单个智能体的对外信息（含中文名 / 启用状态）。"""
    agent = manager.get_agent(name)
    return {
        "name": name,
        "display_name": manager.get_display_name(name),
        "capabilities": agent.get_capabilities(),
        "status": "initialized",
        "enabled": settings.is_agent_enabled(name),
    }


def _skill_info(item: Dict[str, str], settings: Settings) -> Dict[str, Any]:
    """单个技能对外信息（含启用状态）。"""
    return {
        "name": item["name"],
        "display_name": item.get("display_name", item["name"]),
        "category": item.get("category", ""),
        "description": item.get("description", ""),
        "enabled": settings.is_skill_enabled(item["name"]),
    }


def _mcp_info(item: Dict[str, str], settings: Settings) -> Dict[str, Any]:
    """单个 MCP 对外信息（含启用状态）。"""
    return {
        "name": item["name"],
        "display_name": item.get("display_name", item["name"]),
        "transport": item.get("transport", ""),
        "description": item.get("description", ""),
        "enabled": settings.is_mcp_enabled(item["name"]),
    }


def _mask_key(key: str) -> str:
    """密钥脱敏展示：保留前 4 后 4，中间用 **** 代替。"""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _game_name_from(game_name: str, game_dir: str) -> str:
    if game_name:
        return game_name
    if game_dir:
        return Path(game_dir).name
    return "Unknown"


# --------------------------------------------------------------------------- #
# FastAPI 应用
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="Game Doctor Agent API",
    version=__version__,
    description="把 Game Doctor 的多智能体编排器暴露为 HTTP 服务，供原生客户端调用。",
)

logger = logging.getLogger("gamedoctor.server")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常兜底：完整堆栈进日志，响应体带错误类型与信息。

    服务仅监听 127.0.0.1，向本地客户端返回异常类型/信息可直接区分故障点，
    避免客户端只看到光秃秃的 "Internal Server Error" 无法定位。
    """
    logger.error(
        "Unhandled error on %s %s: %s\n%s",
        request.method, request.url.path, exc, traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


@app.get("/")
def root() -> Dict[str, Any]:
    return {"service": "gamedoctor", "version": __version__, "status": "ok"}


@app.get("/agents")
def list_agents() -> List[Dict[str, Any]]:
    """智能体列表（含中文名 / 启用状态）。"""
    manager = get_manager()
    settings = get_settings()
    return [_agent_info(name, manager, settings) for name in manager.list_agent_names()]


@app.get("/skills")
def list_skills() -> List[Dict[str, Any]]:
    """已安装技能列表（含启用状态）。"""
    settings = get_settings()
    return [_skill_info(item, settings) for item in SKILL_CATALOG]


@app.get("/mcps")
def list_mcps() -> List[Dict[str, Any]]:
    """已安装 MCP 列表（含启用状态）。"""
    settings = get_settings()
    return [_mcp_info(item, settings) for item in MCP_CATALOG]


@app.get("/status")
def status() -> Dict[str, Any]:
    """运行状态（当前智能体 / 访问目录 / 最近操作事件）。"""
    return get_tracker().snapshot()


@app.get("/settings")
def get_settings_view() -> Dict[str, Any]:
    """返回设置面板所需数据：LLM 配置（不含明文密钥）、provider 目录（含上次模型）、密钥库、智能体启用。"""
    settings = get_settings()
    manager = get_manager()
    llm = settings.get_llm()
    last_models = settings.provider_models()
    providers = [
        {**p, "last_model": last_models.get(p["id"], "")}
        for p in catalog_providers()
    ]
    return {
        "llm": {
            "provider": llm.get("provider"),
            "model": llm.get("model"),
            "api_base": llm.get("api_base"),
            "has_api_key": bool(llm.get("api_key")),
        },
        "providers": providers,
        "api_keys": [
            {"provider": provider, "masked": _mask_key(key)}
            for provider, key in settings.list_api_keys().items()
        ],
        "agents": [
            {"name": name, "display_name": manager.get_display_name(name),
             "enabled": settings.is_agent_enabled(name)}
            for name in manager.list_agent_names()
        ],
    }


@app.get("/settings/api-key/{provider}")
def get_api_key(provider: str) -> Dict[str, Any]:
    """取某 provider 已保存的 API Key 明文（本地服务，供前端切换厂商时回填）。"""
    return {"provider": provider, "api_key": get_settings().get_api_key(provider)}


@app.post("/settings/api-key")
def save_api_key(req: ApiKeyRequest) -> Dict[str, Any]:
    """保存某 provider 的 API Key 到密钥库。"""
    settings = get_settings()
    settings.set_api_key(req.provider, req.api_key)
    return {"provider": req.provider, "masked": _mask_key(settings.get_api_key(req.provider))}


@app.delete("/settings/api-key/{provider}")
def delete_api_key(provider: str) -> Dict[str, Any]:
    """删除某 provider 已保存的 API Key。"""
    get_settings().delete_api_key(provider)
    return {"provider": provider, "deleted": True}


@app.post("/settings/llm")
def update_llm(req: LlmSettingsRequest) -> Dict[str, Any]:
    """更新 LLM 配置。"""
    get_settings().set_llm(req.provider, req.model, req.api_base, req.api_key)
    llm = get_settings().get_llm()
    return {
        "provider": llm.get("provider"),
        "model": llm.get("model"),
        "api_base": llm.get("api_base"),
    }


@app.post("/settings/agents")
def update_agents(req: AgentsSettingsRequest) -> Dict[str, bool]:
    """批量更新智能体启用开关。"""
    settings = get_settings()
    for name, enabled in req.agents.items():
        settings.set_agent_enabled(name, enabled)
    return settings.enabled_map(list(req.agents.keys()))


@app.post("/settings/skills")
def update_skills(req: ItemsSettingsRequest) -> Dict[str, bool]:
    """批量更新技能启用开关。"""
    settings = get_settings()
    for name, enabled in req.items.items():
        settings.set_skill_enabled(name, enabled)
    return {name: settings.is_skill_enabled(name) for name in req.items.keys()}


@app.post("/settings/mcps")
def update_mcps(req: ItemsSettingsRequest) -> Dict[str, bool]:
    """批量更新 MCP 启用开关。"""
    settings = get_settings()
    for name, enabled in req.items.items():
        settings.set_mcp_enabled(name, enabled)
    return {name: settings.is_mcp_enabled(name) for name in req.items.keys()}


@app.post("/chat")
async def chat(req: ChatRequest) -> Dict[str, Any]:
    """与大模型对话：LLM 自主决策调用智能体并生成回复。"""
    if not req.message.strip():
        return {"reply": "请输入内容。", "agent_used": None, "result": None}
    return await get_chat_service().chat(
        req.message, game_dir=req.game_dir, history=req.history,
        enable_search=req.enable_search, think_level=req.think_level,
        mode=req.mode,
    )


@app.post("/run")
async def run_agent(req: RunRequest) -> Dict[str, Any]:
    """手动执行单个智能体（绕过 LLM 决策）。"""
    manager = get_manager()
    try:
        agent = manager.get_agent(req.agent)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"error": f"未知智能体：{req.agent}",
                     "available": manager.list_agent_names()},
        )

    capability = agent.get_capabilities()[0] if agent.get_capabilities() else "all"
    task = AgentTask(
        name=req.agent,
        required_capabilities=[capability],
        data={
            "game_name": _game_name_from(req.game_name, req.game_dir),
            "game_dir": req.game_dir,
        },
    )
    result = await manager.orchestrator.execute_task(task)
    return _serialize_result(req.agent, result)


@app.post("/analyze")
async def analyze(req: AnalyzeRequest) -> List[Dict[str, Any]]:
    """执行多个（默认全部）智能体，返回结果列表。"""
    manager = get_manager()
    target_names = req.agents if req.agents is not None else manager.list_agent_names()

    results: List[Dict[str, Any]] = []
    for name in target_names:
        if name not in manager.agents:
            results.append({"agent": name, "success": False, "message": "未知智能体", "data": None})
            continue
        agent = manager.get_agent(name)
        capability = agent.get_capabilities()[0] if agent.get_capabilities() else "all"
        task = AgentTask(
            name=name,
            required_capabilities=[capability],
            data={
                "game_name": _game_name_from(req.game_name, req.game_dir),
                "game_dir": req.game_dir,
            },
        )
        result = await manager.orchestrator.execute_task(task)
        results.append(_serialize_result(name, result))
    return results


def main() -> None:
    """进程入口：启动 uvicorn。"""
    import uvicorn

    host = os.environ.get("GAMEDOCTOR_HOST", "127.0.0.1")
    port = int(os.environ.get("GAMEDOCTOR_PORT", "8765"))
    uvicorn.run("gamedoctor.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()