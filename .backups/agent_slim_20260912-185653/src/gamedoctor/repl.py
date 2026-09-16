"""交互式智能体会话（类 Claude Code 的 REPL 启动模式）。

用法：``gamedoctor chat [--game 某游戏]``。进入后：

- 直接输入自然语言（如"帮我检查兼容性"、"分析一下性能"），
  按关键词路由到对应智能体执行；
- 输入斜杠命令（``/help``、``/agents``、``/game``、``/run``、``/exit``）
  做会话控制。

会话本身不依赖 LLM，路由为关键词规则匹配；后续可替换为 LLM 意图识别。
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .agents.base_agent import AgentResult, AgentTask
from .orchestrator.agent_factory import AgentManager, DEFAULT_AGENT_CONFIGS

# 关键词 → (能力标签, 任务名, 中文说明)。顺序即匹配优先级，命中即停。
_INTENT_RULES: List[Tuple[Tuple[str, ...], str, str, str]] = [
    (("兼容", "配置", "能不能玩", "带得动", "要求"), "compatibility", "compatibility_check", "兼容性检测"),
    (("安装", "下载", "装到", "安装路径", "装在哪"), "installation", "installation_plan", "安装规划"),
    (("dlc", "mod", "模组", "扩展包"), "dlc_manager", "dlc_management", "DLC/Mod 管理"),
    (("启动", "加载慢", "进不去", "启动参数"), "launch_optimizer", "launch_optimization", "启动优化"),
    (("存档", "备份", "档丢了", "云存档"), "save_manager", "save_management", "存档管理"),
    (("更新", "版本", "升级", "补丁"), "update_manager", "update_management", "更新管理"),
    (("社区", "攻略", "教程", "视频", "讨论"), "community", "community_aggregation", "社区资源聚合"),
    (("好友", "社交", "语音", "联机好友"), "social", "social_management", "社交管理"),
    (("性能", "帧率", "fps", "卡顿", "掉帧", "优化画面"), "performance", "performance_analysis", "性能分析"),
    (("安全", "反作弊", "病毒", "恶意", "封号"), "security", "security_check", "安全检查"),
    (("音频", "声音", "没声音", "音效", "环绕"), "audio", "audio_analysis", "音频诊断"),
    (("网络", "延迟", "联机", "掉线", "ping", "服务器"), "network", "network_analysis", "网络分析"),
]

_HELP_TEXT = """
**直接输入自然语言**即可，例如：

- 帮我检查一下《GTA5》的兼容性
- 分析一下性能 / 为什么这么卡
- 我的存档帮我备份一下
- 检查一下网络和延迟

**斜杠命令**：

| 命令 | 说明 |
|---|---|
| `/game <名称>` | 设置/查看当前游戏（无参数则显示当前值） |
| `/agents` | 列出所有已注册智能体 |
| `/run <能力标签>` | 直接按能力标签执行，如 `/run performance` |
| `/help` | 显示本帮助 |
| `/exit` 或 `/quit` | 退出会话 |
"""


class AgentREPL:
    """交互式智能体会话。"""

    def __init__(self, default_game: str = "", access_mode: str = "direct"):
        self.console = Console()
        self.game = default_game
        self.history: List[str] = []
        self.access_mode = access_mode if access_mode in ("direct", "sandbox") else "direct"

        # 注册全部智能体并接入编排器
        self.manager = AgentManager()
        self.manager.register_agents(DEFAULT_AGENT_CONFIGS)
        self.manager.setup_orchestrator()

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        """进入 REPL 主循环。"""
        self._print_banner()
        while True:
            try:
                prompt_game = f"[{self.game}] " if self.game else ""
                user_input = self.console.input(f"[bold cyan]你 {prompt_game}>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[dim]再见！[/]")
                break

            if not user_input:
                continue

            self.history.append(user_input)
            if user_input.startswith("/"):
                if self._handle_command(user_input):
                    break
            else:
                self._handle_natural_language(user_input)

    # ------------------------------------------------------------------ #
    # 斜杠命令
    # ------------------------------------------------------------------ #
    def _handle_command(self, text: str) -> bool:
        """处理斜杠命令；返回 True 表示应退出会话。"""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            self.console.print("[dim]再见！[/]")
            return True
        if cmd == "/help":
            self.console.print(Markdown(_HELP_TEXT))
        elif cmd == "/agents":
            self._print_agents()
        elif cmd == "/game":
            if arg:
                self.game = arg
                self.console.print(f"[green]当前游戏已设为：{self.game}[/]")
            else:
                shown = self.game or "（未设置，用 /game <名称> 设置）"
                self.console.print(f"当前游戏：{shown}")
        elif cmd == "/run":
            if not arg:
                self.console.print("[yellow]用法：/run <能力标签>，如 /run performance[/]")
            else:
                self._execute(arg, f"manual_{arg}")
        else:
            self.console.print(f"[yellow]未知命令：{cmd}，输入 /help 查看帮助[/]")
        return False

    # ------------------------------------------------------------------ #
    # 自然语言路由
    # ------------------------------------------------------------------ #
    def _handle_natural_language(self, text: str) -> None:
        """关键词匹配意图并执行对应智能体任务。"""
        lowered = text.lower()
        for keywords, capability, task_name, label in _INTENT_RULES:
            if any(k in lowered for k in keywords):
                self.console.print(f"[dim]→ 路由到 [{label}] 智能体（{capability}）[/]")
                self._execute(capability, task_name)
                return

        # 未命中任何意图：设置了游戏则默认做兼容性检测，否则提示帮助
        if self.game:
            self.console.print("[dim]→ 未识别具体意图，默认执行兼容性检测[/]")
            self._execute("compatibility", "compatibility_check")
        else:
            self.console.print(
                "[yellow]没有识别出意图。输入 /help 查看支持的问法，"
                "或先 /game <名称> 设置游戏。[/]"
            )

    # ------------------------------------------------------------------ #
    # 执行与渲染
    # ------------------------------------------------------------------ #
    def _execute(self, capability: str, task_name: str) -> None:
        """构造任务并经编排器执行，然后渲染结果。"""
        task = AgentTask(
            name=task_name,
            required_capabilities=[capability],
            data={"game_name": self.game or "Unknown"},
        )
        if self.access_mode == "sandbox":
            result = self._execute_sandbox(capability, task)
        else:
            with self.console.status(f"[bold green]正在执行 {capability} ...[/]"):
                result = asyncio.run(self.manager.orchestrator.execute_task(task))
        self._render_result(capability, result)

    # ------------------------------------------------------------------ #
    # 沙箱授权执行（CLI 版三段式：任务授权 → 沙箱执行 → 审核应用/丢弃）
    # ------------------------------------------------------------------ #
    def _execute_sandbox(self, capability: str, task: AgentTask) -> AgentResult:
        """在沙箱会话中执行任务：控制台逐条授权，结束后询问应用或丢弃。"""
        import time

        from .config import app_dir
        from .sandbox import SandboxSession
        from .sandbox.context import AccessContext, use_access

        ticket_id = f"repl-{int(time.time())}"
        root_candidate = Path(self.game) if self.game else None
        if root_candidate is not None and root_candidate.exists():
            root = root_candidate
            task.data["game_dir"] = str(root_candidate)
        else:
            root = app_dir() / "sandbox" / f"{ticket_id}_empty_root"
            root.mkdir(parents=True, exist_ok=True)
        session = SandboxSession(ticket_id, root)
        ctx = AccessContext(
            access_mode="sandbox", ticket_id=ticket_id,
            game_name=self.game or "Unknown", game_dir=str(root), session=session,
        )
        self.console.print(
            f"[cyan]沙箱授权模式：任务 {capability} 执行前需授权（y 批准 / N 拒绝 / a 全部批准）[/]"
        )

        async def _run():
            done = {"v": False}

            async def approver():
                seen = set()
                auto = False
                while not done["v"]:
                    if auto:
                        for req in ctx.gate.list_pending(ticket_id):
                            if req.id not in seen:
                                seen.add(req.id)
                                ctx.gate.decide(req.id, True, "REPL 全部批准")
                    else:
                        for req in ctx.gate.list_pending(ticket_id):
                            if req.id in seen:
                                continue
                            seen.add(req.id)
                            ans = await asyncio.to_thread(
                                self.console.input,
                                f"[yellow]授权请求[/] {req.summary} [y/N/a]> ",
                            )
                            value = ans.strip().lower()
                            approved = value in ("y", "yes", "a", "all")
                            ctx.gate.decide(req.id, approved)
                            auto = value in ("a", "all")
                    await asyncio.sleep(0.2)

            async with use_access(ctx):
                appr = asyncio.create_task(approver())
                try:
                    return await self.manager.orchestrator.execute_task(task)
                finally:
                    done["v"] = True
                    await appr

        result = asyncio.run(_run())
        session.seal()

        changes = list(session.changes.values())
        if changes:
            self.console.print(f"[bold]沙箱暂存 {len(changes)} 项变更：[/]")
            for ch in changes:
                self.console.print(f"  - [{ch.op}] {ch.relpath}")
            answer = self.console.input("[yellow]应用全部变更(a) 还是丢弃(d)？[默认 d]> ")
            if answer.strip().lower() in ("a", "apply", "y", "yes"):
                details = session.apply_changes()
                failed = [d for d in details if not d.get("ok")]
                self.console.print(
                    f"[green]已应用 {len(details) - len(failed)}/{len(details)} 项变更[/]"
                    + (f"，[red]{len(failed)} 项失败[/]" if failed else "")
                )
            else:
                session.discard()
                self.console.print("[dim]已丢弃全部暂存变更，真实文件未改动。[/]")
        else:
            self.console.print("[dim]本次执行没有产生文件变更。[/]")
        return result

    def _render_result(self, capability: str, result: AgentResult) -> None:
        """用 Rich 渲染智能体执行结果。"""
        if not result.success:
            self.console.print(Panel(f"[red]{result.message}[/]", title=f"{capability} 失败", border_style="red"))
            return

        body_lines: List[str] = []
        data = result.data
        if dataclasses.is_dataclass(data) and not isinstance(data, type):
            for key, value in dataclasses.asdict(data).items():
                body_lines.append(f"[bold]{key}[/]: {self._fmt(value)}")
        elif data is not None:
            body_lines.append(self._fmt(data))
        if result.message:
            body_lines.append(f"[dim]{result.message}[/]")

        self.console.print(
            Panel("\n".join(body_lines) or "[green]执行成功[/]",
                  title=f"{capability} 结果", border_style="green")
        )

    @staticmethod
    def _fmt(value: Any) -> str:
        """把任意值格式化成紧凑字符串（列表逐项展开）。"""
        if isinstance(value, (list, tuple)):
            if not value:
                return "（无）"
            return "；".join(str(v) for v in value)
        if isinstance(value, dict):
            if not value:
                return "（无）"
            return "；".join(f"{k}={v}" for k, v in value.items())
        return str(value)

    # ------------------------------------------------------------------ #
    # 展示
    # ------------------------------------------------------------------ #
    def _print_banner(self) -> None:
        agents = self.manager.list_agents()
        self.console.print(Panel(
            f"[bold]Game Doctor 智能体会话[/] v{__version__}\n"
            f"已加载 [green]{len(agents)}[/] 个智能体。"
            "输入自然语言或 /help 查看用法，/exit 退出。",
            border_style="cyan",
        ))

    def _print_agents(self) -> None:
        table = Table(title="已注册智能体")
        table.add_column("名称", style="blue")
        table.add_column("能力标签")
        for agent in self.manager.list_agents():
            table.add_row(agent["name"], ", ".join(agent["capabilities"]))
        self.console.print(table)
