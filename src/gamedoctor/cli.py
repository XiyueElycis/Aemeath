"""Typer CLI 入口（PRD §3 / §4.5 / §4.8）。

命令：
- ``gamedoctor diagnose`` —— 诊断（--fix / --dry-run / --step）
- ``gamedoctor rollback`` —— 一键回滚
- ``gamedoctor feedback`` —— 反馈（沉淀修复模板）
- ``gamedoctor config`` —— 配置与密钥管理
"""

from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from typing import Optional

# Windows 控制台默认 GBK，先统一 stdout/stderr 为 UTF-8，避免 Rich 渲染
# emoji / 中文时抛 UnicodeEncodeError。真实 UTF-8 终端不受影响。
# `reconfigure` 仅存在于运行时 TextIOWrapper 上，静态类型 TextIO 未声明该属性，
# 故用 getattr 探测而非直接访问，避免类型检查器报错。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

import typer
from rich.console import Console

from . import __version__
from .config import Config, load
from .fixer.executor import ExecutionMode, TransactionalExecutor
from .models import RepairAction
from .pipeline import DiagnosisPipeline
from .report import render

app = typer.Typer(
    name="gamedoctor",
    help="通用游戏报错诊断与修复专家（Game Doctor）",
    add_completion=False,
    no_args_is_help=True,
)
config_app = typer.Typer(help="配置与密钥管理")
app.add_typer(config_app, name="config")

_console = Console()


@app.command()
def diagnose(
    game: str = typer.Argument(..., help="游戏名或安装目录"),
    error: str = typer.Option("", "--error", "-e", help="完整错误信息"),
    game_dir: Optional[Path] = typer.Option(None, "--game-dir", "-d", help="游戏安装目录"),
    fix: bool = typer.Option(False, "--fix", help="启用自动修复"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只输出将执行的动作，不实际改动"),
    step: bool = typer.Option(False, "--step", help="单步模式：每步暂停确认"),
) -> None:
    """诊断一个游戏报错，可选自动修复。"""
    if dry_run:
        mode = ExecutionMode.DRY_RUN
    elif step:
        mode = ExecutionMode.STEP
    else:
        mode = ExecutionMode.AUTO

    # 兼容两种入参：既可以是"游戏名"，也可以是"安装目录路径"。
    # 若入参含路径分隔符或指向已存在的目录，则当作安装目录，游戏名取其目录名。
    candidate = Path(game)
    if game_dir is None and (candidate.is_dir() or "/" in game or "\\" in game):
        game_dir = candidate
        game = candidate.name

    # L2 / 单步模式下的逐项确认回调：返回 True 才执行该动作
    def _confirm(action: RepairAction) -> bool:
        return typer.confirm(f"是否执行 [{action.level.value}] {action.description}?")

    pipeline = DiagnosisPipeline()
    if fix:
        pipeline.executor = TransactionalExecutor(confirm=_confirm)

    report = pipeline.run(
        game_name=game,
        error_message=error,
        game_dir=game_dir,
        fix=fix,
        mode=mode,
    )
    render(report)


@app.command()
def rollback(
    diagnosis_id: str = typer.Option(..., "--id", help="诊断 ID，如 20260904-001"),
) -> None:
    """一键回滚某次诊断的自动修复。"""
    executor = TransactionalExecutor()
    try:
        executor.rollback_all(diagnosis_id)
        _console.print(f"[green]已回滚诊断 {diagnosis_id} 的全部改动。[/]")
    except Exception as exc:  # noqa: BLE001
        _console.print(f"[red]回滚失败: {exc}[/]")


@app.command()
def feedback(
    diagnosis_id: str = typer.Option(..., "--id", help="诊断 ID"),
    result: str = typer.Option(..., "--result", help="fixed / partial / failed"),
) -> None:
    """反馈诊断结果，沉淀修复模板（P2）。"""
    if result not in ("fixed", "partial", "failed"):
        raise typer.BadParameter("result 须为 fixed / partial / failed")
    # TODO: 更新知识库命中计数（fixed +1 / failed -1 并标记复查）
    _console.print(f"已记录反馈: {diagnosis_id} -> {result}")


@config_app.command("set-secret")
def set_secret(
    key: str = typer.Argument(..., help="密钥 key，如 llm/deepseek/api_key"),
    value: str = typer.Argument(..., help="密钥值（不会打印）"),
) -> None:
    """把密钥写入系统 Keyring，不明文落盘。"""
    Config().set_secret(key, value)
    _console.print(f"[green]已保存密钥 {key} 到系统 Keyring。[/]")


@config_app.command("path")
def show_path() -> None:
    """显示配置文件路径与备份目录。"""
    cfg = load()
    _console.print(f"配置文件: {cfg.config_path}")
    _console.print(f"数据目录: {cfg.config_path.parent}")


@config_app.command("check-deps")
def check_dependencies() -> None:
    """检查项目依赖状态。"""
    from .utils.dependency_check import DependencyChecker
    DependencyChecker.print_dependency_status()


@app.callback()
def _version_callback(
    version: bool = typer.Option(False, "--version", help="显示版本"),
) -> None:
    if version:
        _console.print(f"gamedoctor {__version__}")
        raise typer.Exit()


@app.command("rag-index")
def rag_index(
    game: str = typer.Argument("", help="游戏名，留空索引所有游戏"),
    force: bool = typer.Option(False, "--force", help="强制重建索引"),
    status: bool = typer.Option(False, "--status", help="显示索引状态"),
) -> None:
    """管理 RAG 知识库索引。"""
    if status:
        from .utils.dependency_check import DependencyChecker

        _console.print("[bold]RAG 索引状态[/]")
        if not DependencyChecker.check_vector_availability():
            _console.print("[red]✗ 向量检索未启用或依赖缺失[/]")
            _console.print("运行 'gamedoctor config check-deps' 查看详情")
            return

        try:
            from .knowledge.retriever import get_available_games
            games = get_available_games()
            _console.print(f"可用的游戏 ({len(games)}):")
            for game in games:
                _console.print(f"  - {game}")
        except Exception as e:
            _console.print(f"[red]获取游戏列表失败: {e}[/]")
    else:
        if not game:
            # 索引所有游戏
            from .knowledge.retriever import check_and_index_games
            _console.print("正在索引所有游戏...")
            result = check_and_index_games()

            if result:
                _console.print(f"[green]索引完成![/]")
                for game_name, count in result.items():
                    _console.print(f"  {game_name}: {count} 个文档")
            else:
                _console.print("[yellow]没有找到需要索引的游戏[/]")
        else:
            # 索引指定游戏
            from .knowledge.retriever import check_and_index_games
            _console.print(f"正在索引游戏: {game}")
            result = check_and_index_games([game])

            if game in result:
                _console.print(f"[green]索引完成![/] {game}: {result[game]} 个文档")
            else:
                _console.print(f"[yellow]游戏 '{game}' 没有找到文档或索引失败[/]")


@app.command("chat")
def chat(
    game: str = typer.Option("", "--game", "-g", help="默认游戏名，会话内可用 /game 修改"),
    access: str = typer.Option(
        "direct", "--access", help="访问模式：direct（直接访问，默认）/ sandbox（沙箱授权）"),
    sandbox: bool = typer.Option(False, "--sandbox", help="沙箱授权模式（等价 --access sandbox）"),
    direct: bool = typer.Option(False, "--direct", help="直接访问模式（等价 --access direct，默认）"),
) -> None:
    """进入交互式智能体会话（类 Claude Code 的 REPL 模式）。"""
    from .repl import AgentREPL

    if sandbox:
        access_mode = "sandbox"
    elif direct:
        access_mode = "direct"
    else:
        access_mode = access
    if access_mode not in ("direct", "sandbox"):
        raise typer.BadParameter(f"非法访问模式：{access_mode}（可选 direct / sandbox）")
    AgentREPL(default_game=game, access_mode=access_mode).run()


@app.command("agents")
def list_agents():
    """列出所有可用的智能体。"""
    from .orchestrator.agent_factory import AgentManager
    from .orchestrator.agent_factory import DEFAULT_AGENT_CONFIGS

    _console.print("[bold]可用的智能体:[/]")
    manager = AgentManager()
    manager.register_agents(DEFAULT_AGENT_CONFIGS)
    manager.setup_orchestrator()

    agents = manager.list_agents()
    for agent in agents:
        _console.print(f"\n[blue]{agent['name']}[/]")
        _console.print(f"  能力: {', '.join(agent['capabilities'])}")
        _console.print(f"  状态: {agent['status']}")


@app.command("analyze")
def analyze_game(
    game: str = typer.Argument(..., help="游戏名或路径"),
    full_analysis: bool = typer.Option(False, "--full", help="执行完整分析"),
):
    """使用智能体分析游戏。"""
    from .orchestrator.agent_factory import AgentManager
    from .orchestrator.agent_factory import DEFAULT_AGENT_CONFIGS
    from .orchestrator.orchestrator import OrchestrationPlan, OrchestrationStage
    from .agents.base_agent import AgentTask

    _console.print(f"[bold]正在分析游戏: {game}[/]")

    # 创建管理器
    manager = AgentManager()
    manager.register_agents(DEFAULT_AGENT_CONFIGS)
    manager.setup_orchestrator()

    # 创建分析工作流
    orchestrator = manager.orchestrator

    if full_analysis:
        # 创建完整分析工作流
        plan = OrchestrationPlan(
            name="full_game_analysis",
            description="Complete game analysis workflow",
            stages=[
                # 第一阶段：基本信息收集
                OrchestrationStage(
                    name="info_collection",
                    tasks=[
                        AgentTask(
                            name="compatibility_check",
                            priority=10,
                            required_capabilities=["compatibility"],
                            data={"game_name": game}
                        ),
                        AgentTask(
                            name="system_analysis",
                            priority=20,
                            required_capabilities=["compatibility"],
                            data={"game_path": game}
                        )
                    ]
                ),
                # 第二阶段：深度分析
                OrchestrationStage(
                    name="deep_analysis",
                    tasks=[
                        AgentTask(
                            name="performance_analysis",
                            priority=30,
                            required_capabilities=["performance"],
                            data={"game": game}
                        ),
                        AgentTask(
                            name="network_analysis",
                            priority=40,
                            required_capabilities=["network"],
                            data={"game": game}
                        )
                    ],
                    dependencies=["info_collection"]
                )
            ]
        )

        # 执行工作流
        result = asyncio.run(orchestrator.execute_workflow(plan, None))

        # 显示结果
        _console.print("\n[bold]分析结果:[/]")
        _console.print(f"成功: {'是' if result['success'] else '否'}")
        _console.print(f"摘要: {result['summary']}")

        # 显示各阶段结果
        for stage_name, stage_result in result["stages"].items():
            _console.print(f"\n[yellow]{stage_name}:[/]")
            for task_name, task_result in stage_result.items():
                if task_result.success:
                    _console.print(f"  [green]{task_name}: 成功[/]")
                else:
                    _console.print(f"  [red]{task_name}: 失败 - {task_result.message}[/]")
    else:
        # 简单的兼容性检查
        compatibility_task = AgentTask(
            name="compatibility_check",
            priority=10,
            required_capabilities=["compatibility"],
            data={"game_name": game}
        )

        result = asyncio.run(manager.orchestrator.execute_task(compatibility_task))
        if result.success:
            _console.print("[green]兼容性检查通过![/]")
        else:
            _console.print(f"[red]兼容性检查失败: {result.message}[/]")


def main() -> None:
    """CLI 主入口（供 ``python -m`` / 打包后的入口脚本调用）。"""
    app()


if __name__ == "__main__":
    main()
