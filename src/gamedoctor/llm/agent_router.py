"""大模型自主决策调用智能体（对话编排）。

对话主链路由 :mod:`gamedoctor.governance` 制度引擎驱动（参照三省六部）：
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

import json
import re
from typing import Any, Dict, List, Optional

from ..governance import GovernanceCoordinator
from ..orchestrator.agent_factory import AgentManager
from ..settings import Settings
from .client import HttpLLMClient


def parse_json(text: str) -> Dict[str, Any]:
    """容错地从 LLM 输出中提取 JSON 对象。

    容忍 `````json ... ``` `` 包裹、前导/尾部闲聊文字，只截取第一个 ``{`` 到
    最后一个 ``}``。解析失败返回空字典（由上层回退处理）。
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


# 思考强度三档：决定温度、最大 token 与 prompt 中要求的推理详略
THINK_LEVELS: Dict[str, Dict[str, Any]] = {
    "light": {"label": "轻度", "temperature": 0.1, "max_tokens": 512, "depth": "简短判断"},
    "medium": {"label": "中度", "temperature": 0.4, "max_tokens": 1024, "depth": "适中分析"},
    "heavy": {"label": "重度", "temperature": 0.7, "max_tokens": 2048, "depth": "深度推理，输出关键步骤"},
}

# 运行模式：edit=编辑模式（可执行文件操作）/ plan=方案模式（只读调查给方案）
MODE_EDIT = "edit"
MODE_PLAN = "plan"

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
        "- 不需要调用、可直接回答时：{\"agent\": null, \"reply\": \"你的回答\", \"reason\": \"思考\"}"
    )

    if mode == MODE_PLAN:
        return (
            "你是游戏辅助安装与排障的分析顾问。当前为【方案模式】：只做调查、不改动任何文件。\n"
            "严禁对文件执行 create/edit/delete/replace 或 download 等破坏性操作，"
            "严禁调用 script_editor 做修改；仅当需要『读文件/列目录/搜索』等只读调查时才可调用智能体，"
            "否则直接输出诊断结论与建议方案。\n\n"
            "可用智能体：\n" + agents_desc + "\n\n" + tail
        )

    return (
        "你是游戏辅助安装与排障的自动化执行者，具备直接操作本地文件的工具。\n"
        "原则：只要任务能被下面的智能体完成，就必须调用对应智能体并返回操作结果，"
        "严禁只输出方案或代码片段让用户手动执行。\n\n"
        "可用智能体：\n" + agents_desc + "\n\n" + tail
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


def generate_reply(client: HttpLLMClient, message: str, display_name: str, result_json: str,
                  model: str, game_dir: str = "", think_level: str = "medium",
                  mode: str = MODE_EDIT) -> str:
    """把智能体/工作流的执行结果回喂 LLM，生成面向用户的自然语言回复。"""
    tcfg = THINK_LEVELS.get(think_level, THINK_LEVELS["medium"])
    if mode == MODE_PLAN:
        system = "你是游戏辅助分析顾问。请用中文、简洁地给出调查结论与建议方案（当前为方案模式，未改动任何文件）。"
    else:
        system = "你是游戏辅助助手。请用中文、简洁地向用户解释下面的执行结果（操作已实际执行）。"
    dir_line = f"当前游戏文件根目录：{game_dir}\n" if game_dir else ""
    user_content = (
        f"用户问题：{message}\n\n"
        f"{dir_line}"
        f"「{display_name}」的执行结果（JSON）：\n{result_json}"
    )
    return client.complete_messages(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        model=model,
        temperature=tcfg["temperature"],
        max_tokens=tcfg["max_tokens"],
    )


class AgentChatService:
    """对话服务（薄壳）：准备 LLM 通道与智能体白名单，委托制度引擎执行。

    真正的协作链路在 :class:`~gamedoctor.governance.GovernanceCoordinator`：
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
                   mode: str = MODE_EDIT, access_mode: str = "direct") -> Dict[str, Any]:
        """处理一轮对话，返回 ``{reply, agent_used, result, thinking, ticket, error?}``。

        :param game_dir: 游戏文件根目录（用于智能体访问路径与状态可视化）
        :param history: 可选的历史消息，供后续多轮扩展使用（当前仅单轮决策）
        :param enable_search: 是否允许大模型联网搜索/下载（默认关）
        :param think_level: 思考强度 light/medium/heavy（默认 medium）
        :param mode: 运行模式 edit（编辑，可执行文件操作）/ plan（方案，只读调查给方案）
        :param access_mode: 访问模式 direct（直接访问）/ sandbox（沙箱授权，
            任务逐个授权、改动先落沙箱待审核）；HTTP 默认由调用方（server）给 sandbox
        """
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
        )
