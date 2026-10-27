"""编程智能体 · 主循环。

与项目里原有的「编排式管线」不同，这是一个真正的 **ReAct 循环**：
模型自己决定下一步调哪个工具，执行结果回灌给模型，再由模型决定继续还是收尾。

一次循环的形态：

    system(角色 + 工具说明 + 输出契约) → user(任务)
      ↑                                    ↓
      └── assistant(JSON: thought + action) ──→ 执行工具 ──→ user(观察结果)

终止条件：模型给出 ``final``、步数用完、或连续重复同一个工具调用。
所有失败都走「已知结局」：要么带着错误信息返回，要么留下已完成的改动，
绝不静默成功。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..llm.agent_router import parse_json
from ..llm.client import HttpLLMClient
from ..settings import Settings, get_settings
from .permissions import PermissionStore
from .sessions import SessionStore
from .tools import (
    ApprovalAsk,
    Approver,
    ToolOutcome,
    describe_tools,
    execute_tool,
)
from .workspace import Workspace

# 单次任务最多走多少步（一步 = 一次模型调用 + 一个工具调用）
MAX_STEPS = 20
# 同一工具 + 同一参数连续重复这么多次就判定为原地打转
REPEAT_LIMIT = 3
# 单次补全上限（够写一段补丁，又不至于把模型拖慢）
MAX_TOKENS = 1600
# 整体硬超时（秒）：避免任务卡死在前端等待
RUN_TIMEOUT = 900
# 上下文预算（字符）。超了就把中段的工具往返压掉，只留最近几步的细节
MAX_CONTEXT_CHARS = 60_000
# 压缩时保留最近多少步的完整往返
KEEP_RECENT_STEPS = 6

_SYSTEM = """你是「爱弥斯」的编程智能体形态，在一台本机电脑上替用户改代码。
你只能通过下面这些工具操作工作区，不能凭空假设文件内容——**任何关于代码的判断都必须先读**。

【可用工具】
{tools}

【输出契约 · 必须严格遵守】
每一步只输出一个 JSON 对象，不要输出任何 JSON 以外的文字、解释或 Markdown 代码块。

要调用工具时（一次一个，或一次多个）：
{{"thought": "为什么这一步要这么做（一句话）",
  "action": {{"tool": "工具名", "args": {{"参数名": "参数值"}}}}}}

{{"thought": "这一步要并行做几件事",
  "actions": [{{"tool": "read_file", "args": {{"path": "a.py"}}}},
              {{"tool": "read_file", "args": {{"path": "b.py"}}}}]}}

任务已经完成时：
{{"thought": "一句话说明结果", "final": "给用户的收尾说明：做了什么、改了哪些文件、验证结果"}}

【工作方式】
1. 面对陌生目录先调 repo_map 拿到「文件 + 符号 + 行号」的概览，再用 grep / read_file 深入；
2. 改动优先用 edit_file（精确、影响面小），整篇重写才用 write_file；
3. 改完用 run_command 跑测试或构建验证；失败就回到第 1 步重新定位；
4. 收尾前用 git_status / git_diff 自查改了什么，别把无关文件带进去；
5. 不要为了"看起来完整"而改动与任务无关的文件；不要新增无谓的抽象层；
6. 找不到就不猜：如实说明缺少什么信息。
7. 用户拒绝某次写入或命令时，不要换个方式硬来——换个思路或直接收尾说明。

【禁止】
- 输出 JSON 之外的内容；编造未读取过的文件内容；
- 执行危险命令（删除工作区外路径、改系统配置、卸载依赖）；
- 声称"已完成"但实际没有调用过写工具或命令。
"""

_JSON_REMINDER = (
    "上一条回复不是合法 JSON。请只输出一个 JSON 对象："
    "要么 {{\"thought\": ..., \"action\": {{\"tool\": ..., \"args\": {{...}}}}}}，"
    "要么 {{\"thought\": ..., \"actions\": [{{\"tool\": ..., \"args\": {{...}}}}, ...]}}，"
    "要么 {{\"thought\": ..., \"final\": ...}}。不要输出 JSON 以外的任何文字。"
)


def _normalize_actions(data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """把 ``action`` / ``actions`` 统一成 [(工具名, 参数), ...]。

    支持一次给多个动作，是为了让「同时读三个文件」「一次改两个模块」不必
    来回好几轮——每一轮都是一次完整的模型调用，省下来的是真金白银。
    """
    raw = data.get("actions")
    if raw is None:
        raw = [data.get("action")] if data.get("action") else []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: List[Tuple[str, Dict[str, Any]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or "").strip()
        if not name:
            continue
        args = item.get("args")
        out.append((name, args if isinstance(args, dict) else {}))
    return out


@dataclass
class AgentStep:
    """一步的留痕（供 CLI 展示与单测断言）。"""

    index: int
    thought: str = ""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    observation: str = ""
    approved: Optional[bool] = None


@dataclass
class AgentResult:
    """一次任务的结果。"""

    finished: bool                  # 模型是否明确给出 final
    final: str = ""
    steps: List[AgentStep] = field(default_factory=list)
    error: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    session_id: str = ""            # 轨迹落盘后的会话 id（可用来 --resume）

    @property
    def tool_calls(self) -> int:
        return len(self.steps)


# 事件回调：(kind, payload)，kind ∈ thought/action/observation/final/error
EventListener = Callable[[str, Dict[str, Any]], None]


class CodingAgent:
    """编程智能体。

    :param workspace: 受限于根目录的工作区
    :param settings: 设置；用于取 provider / 密钥
    :param client: 可替换的 LLM 客户端（单测注入）
    :param approve: 审批回调；None 表示自动放行（CLI 会传交互式实现）
    """

    def __init__(self, workspace: Workspace, *,
                 settings: Optional[Settings] = None,
                 client: Optional[Any] = None, model: Optional[str] = None,
                 approve: Optional[Approver] = None,
                 permissions: Optional[PermissionStore] = None,
                 sessions: Optional[SessionStore] = None,
                 session_id: Optional[str] = None,
                 max_steps: int = MAX_STEPS,
                 on_event: Optional[EventListener] = None) -> None:
        self.workspace = workspace
        self.settings = settings or get_settings()
        self._client = client
        self._model = model
        self.approve = approve
        # 权限记忆：命中长期放行规则时不再打扰用户
        self.permissions = permissions
        self.sessions = sessions
        self.session_id = session_id or ""
        self.max_steps = max(1, int(max_steps))
        self.on_event = on_event

    # ------------------------------------------------------------------ #
    def _resolve_client(self) -> Tuple[Any, Optional[str]]:
        """返回 (客户端, 模型名)；外部注入优先。"""
        if self._client is not None:
            return self._client, self._model
        llm = self.settings.get_llm()
        return HttpLLMClient(
            provider=llm.get("provider"),
            api_base=llm.get("api_base") or None,
            api_key=llm.get("api_key") or None,
        ), self._model or llm.get("model")

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, payload)
        except Exception:  # noqa: BLE001 — 展示层异常不得影响任务
            pass

    def _ask_approval(self, ask: ApprovalAsk) -> bool:
        """审批入口：先查长期放行规则，再问用户。"""
        if self.permissions is not None:
            try:
                if self.permissions.is_allowed(ask):
                    self._emit("approval", {"ask": ask.summary,
                                            "approved": True,
                                            "auto": True})
                    return True
            except Exception:  # noqa: BLE001 — 规则文件坏了就当没放行
                pass
        decided = True if self.approve is None else bool(self.approve(ask))
        self._emit("approval", {"ask": ask.summary, "approved": decided})
        return decided

    # ------------------------------------------------------------------ #
    async def run(self, task: str) -> AgentResult:
        """执行一个编程任务，返回最终结果。"""
        task = (task or "").strip()
        if not task:
            return AgentResult(finished=False, error="任务描述为空")

        client, model = self._resolve_client()
        messages: List[Dict[str, Any]] = [
            {"role": "system",
             "content": _SYSTEM.format(tools=describe_tools())},
        ]
        # 续做：把上次会话的往返塞回来（system 用当前的，工具说明可能已变）
        resumed = self._resume_messages()
        if resumed:
            messages.extend(resumed)
        messages.append({"role": "user",
                         "content": f"【任务】{task}\n【工作区】{self.workspace.root}"})

        result = AgentResult(finished=False, messages=messages)

        try:
            finished = await asyncio.wait_for(
                self._loop(client, model, messages, result), timeout=RUN_TIMEOUT)
        except asyncio.TimeoutError:
            result.error = f"任务超时（{RUN_TIMEOUT}s）"
            self._emit("error", {"message": result.error})
            return self._save(result, task)
        except Exception as exc:  # noqa: BLE001 — 调用层失败也要给出明确结局
            result.error = f"{exc.__class__.__name__}: {exc}"
            self._emit("error", {"message": result.error})
            return self._save(result, task)

        result.finished = finished
        result.messages = messages
        if not finished and not result.error:
            result.error = (
                f"达到步数上限（{self.max_steps} 步）仍未完成，已停止。"
                "可以缩小任务范围后再试，或查看上面的工具记录。")
            self._emit("error", {"message": result.error})
        return self._save(result, task)

    # ------------------------------------------------------------------ #
    def _resume_messages(self) -> List[Dict[str, Any]]:
        """读取待续做的历史（去掉旧 system，避免两套工具说明打架）。"""
        if not (self.sessions and self.session_id):
            return []
        data = self.sessions.load(self.session_id)
        if not data:
            return []
        prior = data.get("messages") or []
        rows = [m for m in prior
                if isinstance(m, dict) and m.get("role") != "system"
                and m.get("content")]
        # 只保留最近若干轮，避免一续做就把上下文顶满
        return rows[-(KEEP_RECENT_STEPS * 2 + 2):]

    def _save(self, result: AgentResult, task: str) -> AgentResult:
        """把轨迹落盘；没有配会话仓库时静默跳过。"""
        if self.sessions is None:
            return result
        try:
            self.session_id = self.sessions.save(
                task=task, root=str(self.workspace.root),
                messages=result.messages,
                steps=[{"index": s.index, "thought": s.thought,
                        "tool": s.tool, "args": s.args, "ok": s.ok,
                        "approved": s.approved,
                        "observation": s.observation} for s in result.steps],
                finished=result.finished, final=result.final,
                error=result.error, session_id=self.session_id or None)
            result.session_id = self.session_id
        except Exception:  # noqa: BLE001 — 落盘失败不影响任务结论
            pass
        return result

    @staticmethod
    def _compact(messages: List[Dict[str, Any]]) -> None:
        """上下文超预算时压缩中段（原地修改）。

        做法刻意保守：保留 system、首个任务、最近几步的完整往返，把更早的
        工具往返换成一行摘要。不调模型做摘要——那会在长任务里再烧一次调用，
        而且一旦摘要写丢关键细节，模型后面会基于错误前提继续干活。
        """
        total = sum(len(str(m.get("content") or "")) for m in messages)
        if total <= MAX_CONTEXT_CHARS or len(messages) < 8:
            return
        head = messages[:2]
        tail_count = KEEP_RECENT_STEPS * 2 + 1
        tail = messages[-tail_count:]
        middle = messages[2:-tail_count]
        if not middle:
            return
        # 中段里出现过的工具名，用来提示模型「这些事已经做过了」
        tools: List[str] = []
        for item in middle:
            text = str(item.get("content") or "")
            if "【工具结果：" in text:
                name = text.split("【工具结果：", 1)[1].split(" · ", 1)[0]
                if name not in tools:
                    tools.append(name)
        note = ("（为节省上下文，中间 {n} 条工具往返已省略；"
                "涉及工具：{tools}。需要细节请重新读取相关文件。）").format(
                    n=len(middle), tools="、".join(tools) or "无")
        messages[:] = head + [{"role": "user", "content": note}] + tail

    async def _loop(self, client: Any, model: Optional[str],
                    messages: List[Dict[str, Any]], result: AgentResult) -> bool:
        """执行主循环；返回模型是否明确收尾。"""
        last_call: Optional[str] = None
        repeats = 0

        for step_no in range(1, self.max_steps + 1):
            self._compact(messages)      # 每次调用前检查预算，长任务不会被撑爆
            raw, data, _drift = await self._ask(client, model, messages)
            messages.append({"role": "assistant", "content": raw})

            if not data:
                result.error = ("模型没有返回可解析的 JSON，任务中止。"
                                + (f"原始输出前 200 字：{raw[:200]}" if raw else ""))
                self._emit("error", {"message": result.error})
                return False

            thought = str(data.get("thought") or "").strip()
            if thought:
                self._emit("thought", {"step": step_no, "text": thought})

            final = data.get("final")
            if isinstance(final, str) and final.strip():
                result.final = final.strip()
                self._emit("final", {"step": step_no, "text": result.final})
                return True

            actions = _normalize_actions(data)
            if not actions:
                # 既没有 final 也没有 action：把 drift 当作一次提醒，别无限重试
                result.error = "模型输出里既没有 action 也没有 final，任务中止。"
                self._emit("error", {"message": result.error})
                return False

            for name, args in actions:
                self._emit("action", {"step": step_no, "tool": name, "args": args})

            # 原地打转守卫：同一组调用重复只会浪费上下文
            signature = json.dumps(actions, ensure_ascii=False, sort_keys=True)
            if signature == last_call:
                repeats += 1
                if repeats >= REPEAT_LIMIT:
                    names = "、".join(n for n, _ in actions)
                    result.error = (
                        f"连续 {REPEAT_LIMIT} 次重复调用「{names}」且参数相同，"
                        "判定为原地打转，任务中止。")
                    self._emit("error", {"message": result.error})
                    return False
            else:
                last_call, repeats = signature, 0

            outcomes = await self._run_actions(actions)
            for (name, args), outcome in zip(actions, outcomes):
                result.steps.append(AgentStep(
                    index=step_no, thought=thought, tool=name, args=args,
                    ok=outcome.ok, observation=outcome.content,
                    approved=outcome.approved))
                self._emit("observation", {
                    "step": step_no, "tool": name, "ok": outcome.ok,
                    "text": outcome.content, "approved": outcome.approved})

            messages.append({"role": "user",
                             "content": self._observe_many(actions, outcomes)})

        return False

    # ------------------------------------------------------------------ #
    async def _run_actions(self, actions: List[Tuple[str, Dict[str, Any]]]
                           ) -> List[ToolOutcome]:
        """执行一组动作：只读的并发，写操作串行。

        并发只读能显著缩短探索耗时；但写操作会改文件、会弹审批、会存快照，
        并发会把「谁先谁后」变成不确定的，因此严格按模型给出的顺序执行。
        """
        from .tools import READONLY_TOOLS

        outcomes: List[Optional[ToolOutcome]] = [None] * len(actions)
        readonly = [(i, name, args) for i, (name, args) in enumerate(actions)
                    if name in READONLY_TOOLS]
        mutating = [(i, name, args) for i, (name, args) in enumerate(actions)
                    if name not in READONLY_TOOLS]

        async def one(item: Tuple[int, str, Dict[str, Any]]) -> None:
            index, name, args = item
            outcomes[index] = await asyncio.to_thread(
                execute_tool, name, args, self.workspace,
                self._ask_approval, self.settings)

        if readonly:
            await asyncio.gather(*(one(item) for item in readonly))
        for item in mutating:
            await one(item)

        return [o if o is not None else ToolOutcome(False, "未执行", "")
                for o in outcomes]

    @staticmethod
    def _observe_many(actions: List[Tuple[str, Dict[str, Any]]],
                      outcomes: List[ToolOutcome]) -> str:
        """多动作的观察文本：按顺序逐条呈现，模型更容易对上号。"""
        if len(actions) == 1 and len(outcomes) == 1:
            return CodingAgent._observe(actions[0][0], outcomes[0])
        blocks = []
        for i, ((name, _args), outcome) in enumerate(zip(actions, outcomes), 1):
            status = "成功" if outcome.ok else "失败"
            blocks.append(f"【工具结果 {i}/{len(actions)}：{name} · {status}】\n"
                          f"{outcome.content or '（无内容）'}")
        blocks.append("（以上为同一轮内的多个动作；失败项请按提示调整。）")
        return "\n\n".join(blocks)

    async def _ask(self, client: Any, model: Optional[str],
                   messages: List[Dict[str, Any]]
                   ) -> Tuple[str, Dict[str, Any], bool]:
        """调模型并解析 JSON；漂移时追加一次严格提醒再试。

        与心动模式同一套策略：第一次解析不出 JSON 就加一条 system 提醒重试，
        仍失败则把原文返回，由调用方决定中止（而不是拿散文当动作执行）。
        """
        raw = ""
        try:
            raw = await asyncio.to_thread(
                client.complete_messages, list(messages),
                model=model, temperature=0.2, max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 — 交给 run() 统一转成错误结局
            raise RuntimeError(f"模型调用失败：{exc}") from exc

        data = parse_json(raw)
        if data:
            return raw, data, False

        retry = list(messages) + [{"role": "system", "content": _JSON_REMINDER}]
        try:
            raw2 = await asyncio.to_thread(
                client.complete_messages, retry,
                model=model, temperature=0.1, max_tokens=MAX_TOKENS)
        except Exception:  # noqa: BLE001 — 重试失败就用首次原文
            return raw, {}, True
        return raw2, parse_json(raw2), True

    @staticmethod
    def _observe(name: str, outcome: ToolOutcome) -> str:
        """把工具结果包装成观察文本。

        显式标注成功/失败与工具名，是为了让模型在长上下文里也能定位
        「刚才那个 read_file 到底读到了什么」。
        """
        status = "成功" if outcome.ok else "失败"
        head = f"【工具结果：{name} · {status}】"
        body = outcome.content or "（无内容）"
        tail = ""
        if not outcome.ok:
            tail = "\n（这一步没有生效，请根据上面的提示调整后再试。）"
        return f"{head}\n{body}{tail}"


async def run_task(task: str, root: str = ".", *,
                   approve: Optional[Approver] = None,
                   settings: Optional[Settings] = None,
                   client: Optional[Any] = None,
                   on_event: Optional[EventListener] = None,
                   max_steps: int = MAX_STEPS) -> AgentResult:
    """便捷入口：给定任务与工作区根目录，跑一次编程任务。"""
    agent = CodingAgent(
        Workspace(root), settings=settings, client=client, approve=approve,
        on_event=on_event, max_steps=max_steps)
    return await agent.run(task)


__all__ = ["CodingAgent", "AgentResult", "AgentStep", "EventListener", "run_task"]
