"""大模型自主决策调用智能体（对话编排）。

对话主链路由 :mod:`aemeath_agent.governance` 制度引擎驱动（参照三省六部）：
**接待官分拣 → 规划官起草 → 审议官强制审议（可封驳）→ 调度官权限派发 →
领域智能体执行 → 验收 → 回奏**。

本模块保留两端收口的 LLM 原语与单智能体决策原语：

- :func:`parse_json`：模型输出容错解析；
- :func:`decide_agent`：接待官直办散点问答时的单智能体决策（1 次 LLM）；
- :func:`generate_reply`：回奏——结构化结果转自然语言（1 次 LLM）。

LLM 调用预算：固定模板路径仅出口 1 次；动态规划路径规划 1 次（封驳每轮
+1，至多 3 次）+ 出口 1 次；审议纯规则不调 LLM。

采用「两轮补全 + 结构化 JSON」而非 provider 私有 function-calling，可泛化到
deepseek / 智谱 GLM / Qwen / Ollama 等任意 OpenAI 兼容端点。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, Dict, List, Optional

from ..governance import GovernanceCoordinator
from ..memory import MemoryManager
from ..orchestrator.agent_factory import AgentManager
from ..persona import apply_persona, decision_hint
from ..settings import Settings
from .client import HttpLLMClient


def _memory_block() -> str:
    """读取用户画像块注入 system prompt（旁路容错，失败不影响调用）。"""
    try:
        return MemoryManager().profile_block()
    except Exception:  # noqa: BLE001
        return ""


def parse_json(text: str) -> Dict[str, Any]:
    """容错地从 LLM 输出中提取 JSON 对象。

    容忍 `````json ... ``` `` 包裹、前导/尾部闲聊文字，解析失败返回空字典
    （由上层回退处理）。

    实现上刻意**逐个 ``{`` 尝试解码**，而不是「首个 ``{`` 到最后一个 ``}``」：
    推理型模型常常在 JSON 后面接着写 ``</think>`` 和半截思考，其中可能又出现
    花括号，按首尾截取会切出一段非法文本；逐点解码只认第一个**完整**对象。
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # ① 整体就是 JSON（最常见，也最快）
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        pass

    # ② 从头扫描每个 '{'，用 raw_decode 找第一个能完整解析的对象
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[index:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return {}


# 思考强度三档：决定温度、最大 token 与 prompt 中要求的推理详略
THINK_LEVELS: Dict[str, Dict[str, Any]] = {
    "light": {"label": "轻度", "temperature": 0.1, "max_tokens": 512, "depth": "简短判断"},
    "medium": {"label": "中度", "temperature": 0.4, "max_tokens": 1024, "depth": "适中分析"},
    "heavy": {"label": "重度", "temperature": 0.7, "max_tokens": 2048, "depth": "深度推理，输出关键步骤"},
}

# 运行模式：edit=编辑模式（可执行文件操作）/ plan=方案模式（只读调查给方案）
# / companion=陪伴模式（仅对话，不调智能体、不操作文件）
MODE_EDIT = "edit"
MODE_PLAN = "plan"
MODE_COMPANION = "companion"

# 陪伴模式带入 LLM 的历史消息条数上限（防超长上下文）
_COMPANION_HISTORY_LIMIT = 20

# 方案模式下禁止的破坏性操作
_MUTATING_OPS = frozenset({"create", "edit", "delete", "replace", "download"})


def _is_mutating(agent_name: str, operation: str) -> bool:
    """判断某次决策是否属于破坏性操作；script_editor 缺省 operation 时视为 edit。"""
    op = (operation or "").lower()
    if op in _MUTATING_OPS:
        return True
    return agent_name == "script_editor" and op in ("", "edit")


def _decision_system(agents_desc: str, depth: str, mode: str) -> str:
    """按运行模式生成「决策」system prompt。"""
    tail = (
        "先做" + depth + "，再输出一个 JSON 对象（不要输出其它任何文字），"
        "JSON 中额外包含 \"reason\" 字段，用一句话记录你的思考过程：\n"
        "- 需要调用智能体时：{\"agent\": \"智能体英文名\", \"question\": \"提炼后的任务描述\", \"reason\": \"思考\"}\n"
        "- 若任务涉及文件操作，用 \"operation\" 表示动作（create/edit/delete/read/list/replace），"
        "并用 \"file\" 表示目标文件路径（相对游戏根目录的路径，或绝对路径）：\n"
        "  * 修改文件 → {\"agent\": \"script_editor\", \"operation\": \"edit\", \"file\": \"路径\", \"reason\": \"思考\"}\n"
        "  * 新建文件 → {\"agent\": \"script_editor\", \"operation\": \"create\", \"file\": \"路径\", \"reason\": \"思考\"}\n"
        "  * 删除文件 → {\"agent\": \"script_editor\", \"operation\": \"delete\", \"file\": \"路径\", \"reason\": \"思考\"}\n"
        "  * 读取文件 → {\"agent\": \"script_editor\", \"operation\": \"read\", \"file\": \"路径\", \"reason\": \"思考\"}\n"
        "  * 列出目录 → {\"agent\": \"script_editor\", \"operation\": \"list\", \"file\": \"目录路径\", \"reason\": \"思考\"}\n"
        "  * 用一个文件替换另一个 → {\"agent\": \"script_editor\", \"operation\": \"replace\", \"file\": \"目标路径\", \"source\": \"源文件路径\", \"reason\": \"思考\"}\n"
        "- 若任务需要联网搜索解决方案，用 web_search 智能体；若需下载文件，加 \"url\" 与 "
        "\"operation\": \"download\"：\n"
        "  * 搜索 → {\"agent\": \"web_search\", \"question\": \"搜索关键词\", \"reason\": \"思考\"}\n"
        "  * 下载 → {\"agent\": \"web_search\", \"operation\": \"download\", \"url\": \"下载地址\", \"file\": \"保存文件名（可省略）\", \"reason\": \"思考\"}\n"
        "- 若需定位报错/崩溃日志，用 log_analyzer 智能体：{\"agent\": \"log_analyzer\", \"question\": \"排查什么报错\", \"reason\": \"思考\"}\n"
        "- 不需要调用、可直接回答时：{\"agent\": null, \"reply\": \"你的回答\", \"reason\": \"思考\"}\n"
        "其中 reply 是用户唯一会读到的文字：用自然的 2~4 句口语写完，"
        "不要分点、不要加粗、不要写标题，可以只回一句。"
    )

    # 人格仅影响 null-agent 直答时 reply 字段的语气（一句话提示），不干扰 JSON 结构
    persona_line = decision_hint()
    persona_clause = f"\n\n{persona_line}" if persona_line else ""

    if mode == MODE_PLAN:
        return (
            "你在帮用户做游戏问题的只读排查与分析，当前为【方案模式】：只做调查、不改动任何文件。\n"
            "严禁对文件执行 create/edit/delete/replace 或 download 等破坏性操作，"
            "严禁调用 script_editor 做修改；仅当需要『读文件/列目录/搜索』等只读调查时才可调用智能体，"
            "否则直接输出诊断结论与建议方案。\n\n"
            "可用智能体：\n" + agents_desc + "\n\n" + tail + persona_clause
        )

    return (
        "你在帮用户处理游戏安装与排障，手上有能直接操作本地文件的智能体。\n"
        "原则：只要任务能被下面的智能体完成，就必须调用对应智能体并返回操作结果，"
        "严禁只输出方案或代码片段让用户手动执行。\n\n"
        "可用智能体：\n" + agents_desc + "\n\n" + tail + persona_clause
    )


def decide_agent(client: HttpLLMClient, message: str, agents_desc: str, model: str,
                 game_dir: str = "", think_level: str = "medium",
                 mode: str = MODE_EDIT) -> Dict[str, Any]:
    """让 LLM 决策调用哪个智能体。返回含 ``agent / reply / question / operation / file / url / source / reason``。"""
    tcfg = THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])
    system = _decision_system(agents_desc, tcfg["depth"], mode)
    # 把用户当前设置的游戏根目录告知 LLM，便于它理解指代（如"这个目录""上面的路径"）
    user_content = message
    if game_dir:
        user_content = f"（当前游戏文件根目录：{game_dir}）\n{message}"
    raw = client.complete_messages(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        model=model,
        temperature=tcfg["temperature"],
        max_tokens=tcfg["max_tokens"],
    )
    return parse_json(raw)


# --------------------------------------------------------------------------- #
# 「说人话」约束：回奏与直答共用，是去掉机器味的关键，删改前请先想清楚
# --------------------------------------------------------------------------- #
_SPEAK_RULES = (
    "\n\n说话要求：\n"
    "- 用跟朋友说话的口吻直接讲，像口头汇报，不像写文档。\n"
    "- 默认 2~4 句。不要小标题、不要分点罗列、不要加粗、不要表格、不要表情符号。\n"
    "- 不要念内部编号、字段名、JSON 原文、任务票号——用户只关心结果和下一步。\n"
    "- 别把结果逐条复述一遍，挑最要紧的先说。\n"
    "- 不要用「首先/其次/最后」「综上所述」「希望对你有帮助」这类套话。\n"
    "- 只有用户明确要求分点，或步骤必须逐条照做时，才用列表。"
)

# 回奏是纯散文输出，温度给足才不会反复套同一批句式；
# 决策/规划路径仍沿用 THINK_LEVELS 的低温度以保 JSON 稳定，不受此影响。
_PROSE_MIN_TEMPERATURE = 0.75


def _prose_temperature(think_level: str) -> float:
    """散文输出（回奏/直答）用的采样温度。"""
    tcfg = THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])
    return max(float(tcfg["temperature"]), _PROSE_MIN_TEMPERATURE)


def _reply_system(mode: str) -> str:
    """回奏 system prompt：任务说明 + 说人话约束 + 完整人格块。

    注意这里**不再声明第二个人格**（旧版写「你是游戏辅助助手」，与人格块里的
    「你是爱弥斯」互相打架，是回复像客服腔的主因）；身份只由人格块给出。
    """
    if mode == MODE_PLAN:
        base = (
            "下面是刚才那次执行结果（本次为【方案模式】，没有改动任何文件），"
            "请把它讲给用户听：说清你查到了什么、建议怎么做。"
        )
    else:
        base = (
            "下面是刚才那次执行结果（操作已经真的执行完了），"
            "请把它讲给用户听：说清做了什么、现在是什么状态、用户接下来要不要做什么。"
        )
    return apply_persona(base + _SPEAK_RULES)


def _reply_user_content(message: str, display_name: str, result_json: str,
                        game_dir: str) -> str:
    """回奏的 user 消息：用户问题 + 执行结果 JSON。"""
    dir_line = f"当前游戏文件根目录：{game_dir}\n" if game_dir else ""
    return (
        f"用户问题：{message}\n\n"
        f"{dir_line}"
        f"「{display_name}」的执行结果（JSON）：\n{result_json}"
    )


def generate_reply(client: HttpLLMClient, message: str, display_name: str, result_json: str,
                  model: str, game_dir: str = "", think_level: str = "medium",
                  mode: str = MODE_EDIT) -> str:
    """把智能体/工作流的执行结果回喂 LLM，生成面向用户的自然语言回复。"""
    return client.complete_messages(
        [
            {"role": "system", "content": _reply_system(mode)},
            {"role": "user",
             "content": _reply_user_content(message, display_name, result_json, game_dir)},
        ],
        model=model,
        temperature=_prose_temperature(think_level),
        max_tokens=THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])["max_tokens"],
    )


def generate_reply_stream(client: HttpLLMClient, message: str, display_name: str,
                         result_json: str, model: str, game_dir: str = "",
                         think_level: str = "medium",
                         mode: str = MODE_EDIT):
    """流式版回奏：逐段 yield 回复文本（供 SSE 推给客户端逐字显示）。

    与 :func:`generate_reply` 共用同一套 system/user 构造，因此仅是「同一段话的
    产生方式不同」，不存在两套语气。
    """
    return client.stream_messages(
        [
            {"role": "system", "content": _reply_system(mode)},
            {"role": "user",
             "content": _reply_user_content(message, display_name, result_json, game_dir)},
        ],
        model=model,
        temperature=_prose_temperature(think_level),
        max_tokens=THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])["max_tokens"],
    )


# 陪伴模式基础指令：人格块之外的行为边界
_COMPANION_BASE = (
    "现在是【陪伴模式】：你只是来陪用户聊天的爱弥斯，不做任何游戏辅助操作——"
    "不调用智能体、不读取或修改文件、不执行命令、不联网搜索或下载。\n"
    "像朋友一样自然地对话：可以聊心情、聊日常、聊星海与冒险，也可以安静温柔地听用户倾诉；"
    "回复保持口语化、长度适中，不要罗列操作建议。\n"
    "如果用户问起游戏故障/安装/排障类的操作问题，只需俏皮地说明『陪伴模式不处理操作类问题，"
    "切回编辑或方案模式我就能帮忙啦』，不要尝试给方案或代码。"
)


def _companion_messages(message: str,
                        history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    """构造陪伴模式的 messages（system 人格块 + 最近若干轮历史 + 当前消息）。

    历史仅保留 role/content 且只取最近 :data:`_COMPANION_HISTORY_LIMIT` 条。
    一次性与流式两条路径共用本函数，保证语气完全一致。
    """
    memory_text = _memory_block()
    companion_base = (
        _COMPANION_BASE + (f"\n\n{memory_text}" if memory_text else ""))
    system = apply_persona(companion_base)
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    cleaned: List[Dict[str, str]] = []
    for item in history or []:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    messages.extend(cleaned[-_COMPANION_HISTORY_LIMIT:])
    messages.append({"role": "user", "content": message})
    return messages


def companion_chat(client: HttpLLMClient, message: str,
                   history: Optional[List[Dict[str, str]]],
                   model: str, think_level: str = "medium") -> str:
    """陪伴模式：一次纯对话 LLM 调用（人格块全开），返回助手文本。"""
    tcfg = THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])
    # 闲聊场景：温度略抬让语气更灵动，token 上限仍随思考档位
    return client.complete_messages(
        _companion_messages(message, history),
        model=model,
        temperature=0.8,
        max_tokens=tcfg["max_tokens"],
    )


def companion_chat_stream(client: HttpLLMClient, message: str,
                          history: Optional[List[Dict[str, str]]],
                          model: str, think_level: str = "medium"):
    """陪伴模式流式版：逐段 yield 回复文本（供 SSE 逐字显示）。"""
    tcfg = THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])
    return client.stream_messages(
        _companion_messages(message, history),
        model=model,
        temperature=0.8,
        max_tokens=tcfg["max_tokens"],
    )


class AgentChatService:
    """对话服务（薄壳）：准备 LLM 通道与智能体白名单，委托制度引擎执行。

    真正的协作链路在 :class:`~aemeath_agent.governance.GovernanceCoordinator`：
    接待→规划→审议（封驳）→派发→执行→验收→回奏。
    """

    def __init__(self, manager: AgentManager, settings: Settings):
        self.manager = manager
        self.settings = settings

    # ------------------------------------------------------------------ #
    def _build_client(self, llm: Dict[str, Any]) -> HttpLLMClient:
        """按运行时设置构造 LLM 客户端（provider/model/api_base/api_key 覆盖配置）。"""
        return HttpLLMClient(
            provider=llm.get("provider"),
            api_base=llm.get("api_base") or None,
            api_key=llm.get("api_key") or None,
        )

    def _enabled_catalog(self, enable_search: bool = False) -> tuple[str, set[str]]:
        """返回（智能体清单文本, 已启用智能体名集合）。

        web_search 仅在「启用联网搜索」开关打开时才对大模型可见，避免模型
        在不该联网时也去搜。其余智能体按设置面板的启用开关过滤；该集合同时
        是审议官与调度官的派发白名单。
        """
        lines: List[str] = []
        enabled: set[str] = set()
        for info in self.manager.list_agents():
            name = info["name"]
            if name == "web_search" and not enable_search:
                continue
            if self.settings.is_agent_enabled(name):
                enabled.add(name)
                caps = "、".join(info["capabilities"])
                lines.append(f"- {name}（{info['display_name']}）: 能力[{caps}]")
        return ("\n".join(lines) or "（无可用智能体）"), enabled

    # ------------------------------------------------------------------ #
    async def chat(self, message: str, game_dir: str = "", history: Optional[List] = None,
                   enable_search: bool = False, think_level: str = "medium",
                   mode: str = MODE_EDIT, access_mode: str = "direct",
                   on_progress: Optional[Callable[[str], None]] = None,
                   on_reply_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """处理一轮对话，返回 ``{reply, agent_used, result, thinking, ticket, error?}``。

        :param game_dir: 游戏文件根目录（用于智能体访问路径与状态可视化）
        :param history: 可选的历史消息，供后续多轮扩展使用（当前仅单轮决策）
        :param enable_search: 是否允许大模型联网搜索/下载（默认关）
        :param think_level: 思考强度 light/medium/heavy（默认 medium）
        :param mode: 运行模式 edit（编辑，可执行文件操作）/ plan（方案，
            只读调查给方案）/ companion（陪伴，仅对话不操作）
        :param access_mode: 访问模式 direct（直接访问）/ sandbox（沙箱授权，
            任务逐个授权、改动先落沙箱待审核）；HTTP 默认由调用方（server）给 sandbox
        """
        # 陪伴模式：制度链全短路，仅一次纯对话
        if mode == MODE_COMPANION:
            return await self._run_companion(message, history, think_level,
                                             on_reply_delta)

        llm = self.settings.get_llm()
        model = llm.get("model")
        client = self._build_client(llm)
        agents_desc, known_agents = self._enabled_catalog(enable_search)

        coordinator = GovernanceCoordinator(self.manager, self.settings)
        return await coordinator.run(
            message,
            client=client,
            model=model,
            agents_desc=agents_desc,
            known_agents=known_agents,
            game_dir=game_dir,
            enable_search=enable_search,
            think_level=think_level,
            mode=mode,
            access_mode=access_mode,
            on_progress=on_progress,
            on_reply_delta=on_reply_delta,
        )

    # ------------------------------------------------------------------ #
    async def _run_companion(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        think_level: str,
        on_reply_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """陪伴模式执行：一次纯对话 LLM 调用，返回契约对齐制度链。

        长期记忆仅在此模式生效：对话入滚动记录，累计到阈值后 LLM 总结
        更新用户画像，画像注入陪伴 system prompt。

        ``on_reply_delta`` 存在时走流式：回复逐段回调，客户端可逐字显示。
        """
        llm = self.settings.get_llm()
        client = self._build_client(llm)
        model = llm.get("model")
        if on_reply_delta is None:
            reply = await asyncio.to_thread(
                companion_chat, client, message, history, model, think_level)
        else:
            def _pump() -> str:
                parts: List[str] = []
                for piece in companion_chat_stream(
                    client, message, history, model, think_level
                ):
                    parts.append(piece)
                    on_reply_delta(piece)
                return "".join(parts)

            reply = await asyncio.to_thread(_pump)

        thinking = ["[陪伴] 陪伴模式：仅对话，未调用智能体、未操作文件"]
        # 记忆旁路：记录本轮 → 阈值达标则异步总结（失败不影响对话）
        if self.settings.is_memory_enabled():
            await self._maybe_remember(message, reply, client,
                                       llm.get("model"), thinking)
        return {
            "reply": reply,
            "agent_used": None,
            "result": None,
            "thinking": thinking,
            "sandbox": None,
        }

    async def _maybe_remember(self, message: str, reply: str,
                               client: HttpLLMClient, model: Optional[str],
                               thinking: List[str]) -> None:
        """记录陪伴对话，达到阈值时触发用户画像总结。"""
        try:
            memory = MemoryManager()
            accumulated = await asyncio.to_thread(memory.record, message, reply)
            threshold = self.settings.get_memory_threshold()
            if accumulated >= threshold > 0:
                thinking.append(
                    f"[记忆] 累计约 {accumulated} tokens，正在总结用户画像…")
                profile = await asyncio.to_thread(memory.summarize, client, model)
                if profile.updated_at:
                    thinking.append(
                        f"[记忆] 用户画像已更新（{profile.updated_at}）")
        except Exception as exc:  # noqa: BLE001 — 记忆旁路不得影响主对话
            thinking.append(f"[记忆] 记录/总结异常已忽略：{type(exc).__name__}")
