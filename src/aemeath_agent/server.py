"""Aemeath Agent 智能体 HTTP 后端（FastAPI）。

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

启动：``aemeath_agent-server`` 或 ``uvicorn aemeath_agent.server:app``。
默认监听 ``127.0.0.1:8765``（可用环境变量 ``AEMEATH_AGENT_HOST`` / ``AEMEATH_AGENT_PORT`` 覆盖）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .agents.base_agent import AgentResult, AgentTask
from .galgame import GalgameEngine, GalgameMemory
from .galgame.state import load_save as load_galgame_save
from .knowledge.game_names import display_name as game_display_name
from .llm.agent_router import AgentChatService
from .llm.catalog import providers as catalog_providers
from .mcps import MCP_CATALOG
from .orchestrator.agent_factory import AgentManager, DEFAULT_AGENT_CONFIGS
from .runtime import get_tracker
from .sandbox.gate import get_gate
from .sandbox.session import (
    SandboxSession,
    SandboxStateError,
    SandboxStatus,
    get_registered_session,
    registered_sessions,
)
from .settings import Settings, get_settings
from .skills import SKILL_CATALOG

# --------------------------------------------------------------------------- #
# 全局单例：复用同一套智能体 + 编排器 + 设置 + 对话服务
# --------------------------------------------------------------------------- #
_manager: Optional[AgentManager] = None
_chat_service: Optional[AgentChatService] = None
_galgame_engine: Optional[GalgameEngine] = None


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


def get_galgame_engine() -> GalgameEngine:
    """心动模式引擎单例（接入长期记忆：独立目录 + 只读共享画像）。"""
    global _galgame_engine
    if _galgame_engine is None:
        _galgame_engine = GalgameEngine(get_settings(), memory=GalgameMemory())
    return _galgame_engine


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
    # 运行模式 edit（编辑）/ plan（方案，只读调查）/ companion（陪伴，仅对话）
    mode: str = "edit"
    # 访问模式 sandbox（沙箱授权，HTTP 默认）/ direct（直接访问）
    access_mode: str = "sandbox"


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


class GalgameChatRequest(BaseModel):
    """心动模式：一轮玩家消息。"""
    message: str = ""


class GalgameProactiveRequest(BaseModel):
    """心动模式：请求她主动开口（resume 久别问候 / idle 闲置搭话）。"""
    reason: str = "resume"


class GalgameStarRequest(BaseModel):
    """心动模式：收藏 / 取消收藏一条回忆（mid 为记忆指纹）。"""
    mid: str = ""
    star: bool = True


class CodingRunRequest(BaseModel):
    """编程智能体：一次任务。

    ``mode``：plan=只读（拒绝写操作与命令）/ auto=自动放行（默认 plan）。
    ``session``：给会话 id 表示接着上次做（空则新开一份）。
    """
    task: str = ""
    root: str = "."
    mode: str = "plan"
    max_steps: int = 20
    session: str = ""


class ItemsSettingsRequest(BaseModel):
    items: Dict[str, bool]  # name -> enabled（Skill / MCP 通用）


class McpServerRequest(BaseModel):
    """注册一个外部 MCP 服务器（stdio）。

    ``command`` 可以是字符串或字符串数组；数组形式更适合带参数的命令
    （如 ``["npx", "-y", "@scope/server"]``）。
    """
    name: str = ""
    command: Any = None
    cwd: str = ""
    enabled: bool = True
    timeout: float = 30.0


class PersonaSettingsRequest(BaseModel):
    enabled: bool


class ApprovalDecisionRequest(BaseModel):
    """对一条任务授权请求的裁决。"""
    decision: str  # approve / reject
    reason: str = ""


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
    """任务展示名：显式传入优先，否则取目录名，统一走别名库规范成显示名。"""
    raw = game_name or (Path(game_dir).name if game_dir else "")
    return game_display_name(raw) if raw else "Unknown"


# --------------------------------------------------------------------------- #
# FastAPI 应用
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="Aemeath Agent API",
    version=__version__,
    description="把 Aemeath Agent 的多智能体编排器暴露为 HTTP 服务，供原生客户端调用。",
)

logger = logging.getLogger("aemeath_agent.server")


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
    return {"service": "aemeath_agent", "version": __version__, "status": "ok"}


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


@app.get("/mcps/servers")
def list_mcp_servers() -> List[Dict[str, Any]]:
    """已注册的外部 MCP 服务器（真正可以启动的那些）。"""
    return get_settings().get_mcp_servers()


@app.put("/mcps/servers")
def upsert_mcp_server(req: McpServerRequest) -> Dict[str, Any]:
    """新增或更新一个 MCP 服务器（按 name 去重）。"""
    try:
        return get_settings().upsert_mcp_server(
            req.name, req.command, req.cwd, req.enabled, req.timeout)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/mcps/servers/{name}")
def delete_mcp_server(name: str) -> Dict[str, Any]:
    """删除一个 MCP 服务器注册。"""
    removed = get_settings().remove_mcp_server(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"没有名为「{name}」的服务器")
    return {"removed": name}


@app.post("/mcps/servers/{name}/probe")
def probe_mcp_server(name: str) -> Dict[str, Any]:
    """试着启动某个服务器并列出工具（失败不抛 500，返回可用状态）。"""
    from .coding.mcp_client import MCPError, StdioMCPClient

    servers = {s.get("name"): s for s in get_settings().get_mcp_servers()}
    row = servers.get(name)
    if not row:
        raise HTTPException(status_code=404, detail=f"没有名为「{name}」的服务器")
    command = row.get("command")
    if isinstance(command, str):
        command = [command]
    client = StdioMCPClient([str(c) for c in (command or [])],
                            cwd=row.get("cwd") or None,
                            timeout=float(row.get("timeout") or 30.0))
    try:
        info = client.start()
        tools = client.list_tools()
        return {"ok": True, "server": info,
                "tools": [{"name": t.get("name"),
                           "description": t.get("description", "")}
                          for t in tools]}
    except MCPError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        client.close()


@app.post("/mcps/servers/close")
def close_mcp_servers() -> Dict[str, Any]:
    """关闭所有已建立的 MCP 连接（下次调用会重新拉起）。"""
    from .coding import mcp_servers

    mcp_servers.close_all()
    return {"closed": True}


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
        "persona": {"enabled": settings.is_persona_enabled()},
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


@app.post("/settings/persona")
def update_persona(req: PersonaSettingsRequest) -> Dict[str, bool]:
    """更新角色人格开关，返回最新状态。"""
    get_settings().set_persona_enabled(req.enabled)
    return {"enabled": get_settings().is_persona_enabled()}


@app.post("/chat")
async def chat(req: ChatRequest):
    """与大模型对话：LLM 自主决策调用智能体并生成回复。"""
    if req.access_mode not in ("sandbox", "direct"):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_access_mode",
                     "detail": f"非法访问模式：{req.access_mode}（可选：sandbox / direct）"},
        )
    if req.mode not in ("edit", "plan", "companion"):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_mode",
                     "detail": f"非法运行模式：{req.mode}（可选：edit / plan / companion）"},
        )
    if not req.message.strip():
        return {"reply": "请输入内容。", "agent_used": None,
                "result": None, "sandbox": None}
    return await get_chat_service().chat(
        req.message, game_dir=req.game_dir, history=req.history,
        enable_search=req.enable_search, think_level=req.think_level,
        mode=req.mode, access_mode=req.access_mode,
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """与大模型对话（SSE 流式版）：进度与回复增量实时推送。

    事件格式为每行 ``data: {json}``：

    - ``{"type":"progress","text":"…"}`` —— 人话版进度（现在做到哪一步了）
    - ``{"type":"delta","text":"…"}``    —— 回复增量（客户端逐字追加即可）
    - ``{"type":"done","payload":{…}}``  —— 完整结果，结构与 ``POST /chat`` 一致
    - ``{"type":"error","message":"…"}`` —— 出错

    ``/chat`` 保持原样不动：本端点是纯新增，旧客户端与既有测试不受影响。
    """
    if req.access_mode not in ("sandbox", "direct"):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_access_mode",
                     "detail": f"非法访问模式：{req.access_mode}（可选：sandbox / direct）"},
        )
    if req.mode not in ("edit", "plan", "companion"):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_mode",
                     "detail": f"非法运行模式：{req.mode}（可选：edit / plan / companion）"},
        )

    async def _events():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        sentinel = object()

        def _emit(item: Any) -> None:
            # 回调可能来自工作线程（回奏/走进陪伴流式时在 to_thread 中执行），
            # 必须跨线程投递，否则往 asyncio.Queue 直接写并不安全。
            loop.call_soon_threadsafe(queue.put_nowait, item)

        async def _run() -> None:
            try:
                if not req.message.strip():
                    payload: Dict[str, Any] = {
                        "reply": "请输入内容。", "agent_used": None, "result": None,
                        "thinking": [], "narration": [], "sandbox": None,
                    }
                else:
                    payload = await get_chat_service().chat(
                        req.message, game_dir=req.game_dir, history=req.history,
                        enable_search=req.enable_search, think_level=req.think_level,
                        mode=req.mode, access_mode=req.access_mode,
                        on_progress=lambda text: _emit(
                            {"type": "progress", "text": text}),
                        on_reply_delta=lambda text: _emit(
                            {"type": "delta", "text": text}),
                    )
                _emit({"type": "done", "payload": payload})
            except Exception as exc:  # noqa: BLE001 — 流内异常转为 error 事件
                logger.error("chat/stream 失败：%s\n%s", exc, traceback.format_exc())
                _emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                _emit(sentinel)

        task = asyncio.create_task(_run())
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        await task

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# 任务授权（沙箱模式三段式·第二段的用户裁决通道）
# --------------------------------------------------------------------------- #
@app.get("/approvals/pending")
def list_pending_approvals(ticket_id: Optional[str] = None) -> Dict[str, Any]:
    """列出待决任务授权请求（可按票据过滤）。"""
    items = get_gate().list_pending(ticket_id)
    return {"items": [r.to_dict() for r in items], "count": len(items)}


@app.post("/approvals/{request_id}")
def decide_approval(request_id: str, req: ApprovalDecisionRequest) -> Dict[str, Any]:
    """对一条授权请求批准/拒绝；未知 id → 404，重复裁决 → 409。"""
    if req.decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail=f"decision 只能是 approve / reject，收到：{req.decision}")
    gate = get_gate()
    if not any(r.id == request_id for r in gate.list_pending()):
        # 已裁决（决定即时留痕）→ 409；从未存在 → 404
        if gate.is_decided(request_id):
            raise HTTPException(status_code=409, detail="授权请求已被裁决，不能重复决定")
        raise HTTPException(status_code=404, detail="授权请求不存在或已过期")
    accepted = gate.decide(
        request_id, approved=(req.decision == "approve"), reason=req.reason)
    if not accepted:
        raise HTTPException(status_code=409, detail="授权请求已被裁决，不能重复决定")
    return {"id": request_id, "decision": req.decision, "accepted": True}


# --------------------------------------------------------------------------- #
# 沙箱会话审核（三段式·第三段：查看变更 / 应用 / 丢弃）
# --------------------------------------------------------------------------- #
def _resolve_session(ticket_id: str) -> SandboxSession:
    """先查内存实例，缺失则从磁盘 meta 加载（模拟重启后的遗留会话）。"""
    session = get_registered_session(ticket_id)
    if session is not None:
        return session
    try:
        return SandboxSession.load(ticket_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"沙箱会话不存在：{ticket_id}")


@app.get("/sandbox/pending")
def list_pending_sandboxes() -> Dict[str, Any]:
    """列出待审核（ready）会话：内存注册表 + 磁盘 meta 双路径合并去重。"""
    items: Dict[str, Dict[str, Any]] = {}
    for s in registered_sessions(SandboxStatus.READY):
        items[s.ticket_id] = {
            "ticket_id": s.ticket_id,
            "real_root": str(s.real_root or ""),
            "status": s.status,
            "updated_at": s.updated_at,
            "change_count": len(s.changes),
        }
    for info in SandboxSession.list_sessions(status=SandboxStatus.READY):
        items.setdefault(info["ticket_id"], info)
    ordered = sorted(items.values(), key=lambda x: x.get("updated_at", ""))
    return {"items": ordered, "count": len(ordered)}


@app.get("/sandbox/sessions/{ticket_id}")
def get_sandbox_session(ticket_id: str, preview: bool = True) -> Dict[str, Any]:
    """会话详情：变更清单；文本类变更附带 new/old 预览（过大截断标记）。"""
    session = _resolve_session(ticket_id)
    detail = session.describe()
    if preview:
        previews = []
        for change in detail["changes"]:
            try:
                previews.append(session.change_content(change["key"], limit=50_000))
            except (OSError, KeyError):
                previews.append(change)
        detail["changes"] = previews
    return detail


@app.post("/sandbox/sessions/{ticket_id}/apply")
def apply_sandbox(ticket_id: str) -> Dict[str, Any]:
    """应用全部变更（先统一备份再落盘）；running/已处理会话 → 409。"""
    session = _resolve_session(ticket_id)
    if session.status == SandboxStatus.RUNNING:
        raise HTTPException(status_code=409, detail="会话仍在执行中，尚未封存，不能应用")
    try:
        details = session.apply_changes()
    except SandboxStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    failures = [d for d in details if not d.get("ok")]
    return {
        "ticket_id": ticket_id,
        "status": session.status,
        "total": len(details),
        "succeeded": len(details) - len(failures),
        "failed": len(failures),
        "details": details,
    }


@app.post("/sandbox/sessions/{ticket_id}/discard")
def discard_sandbox(ticket_id: str) -> Dict[str, Any]:
    """丢弃全部暂存变更（真实文件系统零变化）；running/已处理会话 → 409。"""
    session = _resolve_session(ticket_id)
    if session.status == SandboxStatus.RUNNING:
        raise HTTPException(status_code=409, detail="会话仍在执行中，尚未封存，不能丢弃")
    try:
        session.discard()
    except SandboxStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ticket_id": ticket_id, "discarded": True}


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


# --------------------------------------------------------------------------- #
# 心动模式（Galgame）API
# 注意：以下 /galgame/api/* 路由必须先于 /galgame 的静态目录挂载注册，
#       否则会被 StaticFiles 接管而返回 404。
# --------------------------------------------------------------------------- #
@app.get("/galgame/api/state")
async def galgame_state() -> Dict[str, Any]:
    """当前存档公开视图（未开始时也返回默认状态）。"""
    return get_galgame_engine().public_state(load_galgame_save())


@app.post("/galgame/api/start")
async def galgame_start() -> Dict[str, Any]:
    """开启或继续一段周目。"""
    return get_galgame_engine().start()


@app.post("/galgame/api/chat")
async def galgame_chat(req: GalgameChatRequest) -> Dict[str, Any]:
    """处理一轮玩家对话（好感度 / 事件 / 章节推进）。"""
    return await get_galgame_engine().chat(req.message)


@app.post("/galgame/api/proactive")
async def galgame_proactive(req: GalgameProactiveRequest) -> Dict[str, Any]:
    """她主动开口（久别问候 / 闲置搭话）；受冷却与每日上限约束。"""
    return await get_galgame_engine().proactive(req.reason)


@app.post("/galgame/api/reset")
async def galgame_reset() -> Dict[str, Any]:
    """重新开始（当前周目会先归档，不再直接抹掉回忆）。"""
    return get_galgame_engine().reset()


@app.get("/galgame/api/art")
async def galgame_art() -> Dict[str, str]:
    """章节背景 / 事件 CG / 结局 CG 的 URL 全集。

    单独成接口：这份清单有二十多条长 URL，若跟着每轮对话下发纯属浪费带宽，
    前端启动时取一次并缓存即可。
    """
    return get_galgame_engine().art_catalog()


@app.get("/galgame/api/archives")
async def galgame_archives() -> List[Dict[str, Any]]:
    """往期周目摘要（最新在前）。"""
    return get_galgame_engine().archives()


@app.post("/galgame/api/memory/star")
async def galgame_memory_star(req: GalgameStarRequest) -> Dict[str, Any]:
    """收藏 / 取消收藏一条回忆（按指纹定位）。"""
    return get_galgame_engine().toggle_star(req.mid, req.star)


# --------------------------------------------------------------------------- #
# 编程智能体（Coding Agent）
# 与上面「智能体编排」不同：这里跑的是真正的 ReAct 循环，模型自己决定调哪个工具。
# --------------------------------------------------------------------------- #
@app.get("/coding/tools")
async def coding_tools() -> List[Dict[str, Any]]:
    """可用工具清单（名称 / 说明 / 参数 / 是否需要审批）。"""
    from .coding.tools import READONLY_TOOLS, TOOL_SPECS

    return [{
        "name": name,
        "description": spec["description"],
        "args": spec["args"],
        "approval": name not in READONLY_TOOLS,
    } for name, spec in TOOL_SPECS.items()]


@app.post("/coding/run")
async def coding_run(req: CodingRunRequest) -> Dict[str, Any]:
    """在指定工作区执行一次编程任务，返回完整轨迹。

    ``mode``：``plan`` 只读（拒绝一切写入与命令）/ ``auto`` 自动放行。
    HTTP 侧没有交互式审批通道，因此不提供 ``ask`` 模式——需要逐步确认请用
    命令行 ``python -m aemeath_agent.coding``。
    """
    from .coding import CodingAgent, Workspace, WorkspaceError
    from .coding.sessions import SessionStore

    task = (req.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="task 不能为空")
    try:
        workspace = Workspace(req.root or ".")
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    auto = req.mode == "auto"
    agent = CodingAgent(
        workspace, settings=get_settings(),
        approve=(lambda ask: True) if auto else (lambda ask: False),
        sessions=SessionStore(),
        session_id=req.session or None,
        max_steps=max(1, min(int(req.max_steps or 20), 50)),
    )
    result = await agent.run(task)
    return {
        "root": str(workspace.root),
        "mode": "auto" if auto else "plan",
        "finished": result.finished,
        "final": result.final,
        "error": result.error,
        "session": result.session_id,
        "steps": [{
            "index": step.index,
            "thought": step.thought,
            "tool": step.tool,
            "args": step.args,
            "ok": step.ok,
            "approved": step.approved,
            "observation": step.observation,
        } for step in result.steps],
    }


@app.get("/coding/sessions")
async def coding_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    """最近的编程会话摘要（最新在前）。"""
    from .coding.sessions import SessionStore

    return SessionStore().list(limit=max(1, min(int(limit), 100)))


@app.post("/coding/run/stream")
async def coding_run_stream(req: CodingRunRequest):
    """与 ``/coding/run`` 相同，但以 SSE 逐步推送 thought / action / observation。

    前端能边跑边看，不必等整段任务结束；事件格式与 CLI 的回调一致。
    """
    import asyncio as _asyncio

    from .coding import CodingAgent, Workspace, WorkspaceError
    from .coding.sessions import SessionStore

    task = (req.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="task 不能为空")
    try:
        workspace = Workspace(req.root or ".")
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    auto = req.mode == "auto"
    queue: "_asyncio.Queue[Dict[str, Any]]" = _asyncio.Queue()

    def on_event(kind: str, payload: Dict[str, Any]) -> None:
        queue.put_nowait({"event": kind, **payload})

    agent = CodingAgent(
        workspace, settings=get_settings(),
        approve=(lambda ask: True) if auto else (lambda ask: False),
        sessions=SessionStore(),
        session_id=req.session or None,
        max_steps=max(1, min(int(req.max_steps or 20), 50)),
        on_event=on_event,
    )

    async def run_and_close() -> None:
        result = await agent.run(task)
        queue.put_nowait({
            "event": "done", "finished": result.finished,
            "final": result.final, "error": result.error,
            "session": result.session_id,
            "steps": len(result.steps),
        })

    async def stream():
        worker = _asyncio.create_task(run_and_close())
        try:
            while True:
                try:
                    item = await _asyncio.wait_for(queue.get(), timeout=15.0)
                except _asyncio.TimeoutError:
                    yield ": keep-alive\n\n"      # 心跳，避免代理掐断长连接
                    continue
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
                if item.get("event") == "done":
                    break
        finally:
            if not worker.done():
                worker.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


# 静态页面（index.html / styles.css / app.js）
_GALGAME_WEB_DIR = Path(__file__).parent / "galgame" / "web"
app.mount(
    "/galgame",
    StaticFiles(directory=str(_GALGAME_WEB_DIR), html=True),
    name="galgame",
)

# --------------------------------------------------------------------------- #
# 科技终端风格启动动画（WPF StartupWindow 的 WebView2 全屏加载）
# 仅静态资源：index.html / style.css / boot.js / assets/brand.png
# --------------------------------------------------------------------------- #
_STARTUP_WEB_DIR = Path(__file__).parent / "startup" / "web"
app.mount(
    "/startup",
    StaticFiles(directory=str(_STARTUP_WEB_DIR), html=True),
    name="startup",
)


def main() -> None:
    """进程入口：启动 uvicorn。"""
    import uvicorn

    host = os.environ.get("AEMEATH_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("AEMEATH_AGENT_PORT", "8765"))
    uvicorn.run("aemeath_agent.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()