"""交互式诊断会话 CLI（PRD §4.4 首轮三要素 + 多轮追问）。

区别于 ``src/gamedoctor/cli.py`` 的一次性参数方式：本命令逐轮问答，补齐
「游戏名 + 平台 / 完整错误信息 / 发生时机」三要素；信息不足时明确追问、不硬猜。
收集完成后仍复用 :class:`~gamedoctor.pipeline.DiagnosisPipeline` 完成诊断与
（可选）自动修复，不复制核心逻辑。

用法（在项目根目录下执行）：:

    python -m CLI.cli
    python -m CLI.cli chat
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# CLI 位于项目根目录，独立于已安装的 gamedoctor 包；先把 src/ 加入 sys.path，
# 这样即使未执行 `pip install -e .` 也能直接 `python -m CLI.cli` 运行。
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Windows 控制台默认 GBK，统一 stdout/stderr 为 UTF-8，避免 Rich 渲染中文/emoji 报错。
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

from gamedoctor.fixer.executor import ExecutionMode, TransactionalExecutor
from gamedoctor.models import RepairAction
from gamedoctor.pipeline import DiagnosisPipeline
from gamedoctor.report import render

app = typer.Typer(name="gamedoctor-chat", add_completion=False, no_args_is_help=True)
console = Console()


# --------------------------------------------------------------------------- #
# 会话上下文（逐步收集的诊断信息）
# --------------------------------------------------------------------------- #


@dataclass
class DiagnosisRequest:
    """交互式会话逐步收集的诊断上下文（对应 PRD §4.4 三要素）。"""

    game_name: str | None = None
    platform: str | None = None          # steam / epic / gog / standalone / emulator
    game_dir: Path | None = None
    error_message: str | None = None     # 完整错误信息 / 弹窗文字
    timing: str | None = None            # 发生时机：更新后 / 装 Mod 后 / 一直有
    log_paths: list[Path] = field(default_factory=list)

    def missing(self) -> list[str]:
        """返回仍缺失的关键项中文名，用于明确列出并定向追问。"""
        out: list[str] = []
        if not self.game_name:
            out.append("游戏名")
        if not self.platform:
            out.append("平台")
        if not self.error_message:
            out.append("完整错误信息")
        if not self.timing:
            out.append("发生时机")
        return out


# --------------------------------------------------------------------------- #
# 交互式会话
# --------------------------------------------------------------------------- #


class InteractiveSession:
    """交互式诊断会话（PRD §4.4「首轮必问三要素 + 多轮追问」）。"""

    MAX_ROUNDS = 3  # 追问轮数上限，避免信息始终不足时陷入死循环

    def run(self) -> None:
        """收集信息 → 选择修复模式 → 调用诊断管线并渲染结果。"""
        info = DiagnosisRequest()
        self._first_round(info)
        self._follow_ups(info)
        info.game_dir = info.game_dir or self._resolve_dir(info)

        fix = typer.confirm("是否启用自动修复（--fix）？", default=False)
        mode = ExecutionMode.AUTO
        if fix:
            mode = self._pick_mode()

        console.print(f"\n[bold]开始诊断：{info.game_name}[/]\n")
        report = self._invoke(info, fix=fix, mode=mode)
        render(report)

    # -- 问答各步 ---------------------------------------------------------- #
    def _first_round(self, info: DiagnosisRequest) -> None:
        """首轮必问三要素（PRD §4.4）。"""
        console.print("[cyan]—— 首轮三要素 ——[/]")
        info.game_name = typer.prompt("① 游戏名（如 GTA5 / Minecraft）")
        info.platform = typer.prompt("   平台", default="standalone", show_default=True)
        info.error_message = typer.prompt("② 完整错误信息（弹窗文字 / 报错日志）", default="")
        info.timing = typer.prompt("③ 发生时机（更新后 / 装Mod后 / 一直有 / 首次启动）",
                                   default="一直有")

    def _follow_ups(self, info: DiagnosisRequest) -> None:
        """信息不足不硬猜：明确列出缺失项并循环追问，直到补齐或达到轮数上限。"""
        for _ in range(self.MAX_ROUNDS):
            missing = info.missing()
            if not missing:
                return
            console.print(f"[yellow]仍缺少：{'、'.join(missing)}[/]")
            for item in missing:
                self._ask_one(info, item)

    def _ask_one(self, info: DiagnosisRequest, item: str) -> None:
        """针对单个缺失项定向追问，并顺带收集可选的日志路径。"""
        if item == "游戏名":
            info.game_name = typer.prompt("请补充：游戏名称")
        elif item == "平台":
            info.platform = typer.prompt("请补充：运行平台", default="standalone")
        elif item == "完整错误信息":
            info.error_message = typer.prompt("请粘贴完整报错文字")
            log_path = typer.prompt("（可选）能否提供日志文件路径？留空跳过", default="")
            if log_path:
                info.log_paths.append(Path(log_path))
        elif item == "发生时机":
            info.timing = typer.prompt("请补充：问题发生时机", default="一直有")

    def _resolve_dir(self, info: DiagnosisRequest) -> Path | None:
        """由游戏名/平台推断安装目录；推断能力未实现前先手动询问。

        平台适配层的安装目录自动定位尚未落地（PRD §4.1 TODO），这里允许留空，
        仅依赖错误信息也能走后续诊断。
        """
        guess = typer.prompt("游戏安装目录（留空跳过，仅依赖错误信息诊断）", default="")
        return Path(guess) if guess else None

    def _pick_mode(self) -> ExecutionMode:
        """启用 --fix 时进一步选择执行模式。"""
        if typer.confirm("干跑模式（--dry-run，只看不动）？", default=False):
            return ExecutionMode.DRY_RUN
        if typer.confirm("单步模式（--step，每个动作逐项确认）？", default=False):
            return ExecutionMode.STEP
        return ExecutionMode.AUTO

    # -- 复用诊断管线 ------------------------------------------------------ #
    def _invoke(self, info: DiagnosisRequest, fix: bool, mode: ExecutionMode):
        """把收集到的信息交给 :class:`DiagnosisPipeline`，与 ``diagnose`` 一致。"""

        def _confirm(action: RepairAction) -> bool:
            return typer.confirm(f"是否执行 [{action.level.value}] {action.description}?")

        pipeline = DiagnosisPipeline()
        if fix:
            pipeline.executor = TransactionalExecutor(confirm=_confirm)

        return pipeline.run(
            game_name=info.game_name or "未知游戏",
            error_message=info.error_message or "",
            game_dir=info.game_dir,
            fix=fix,
            mode=mode,
        )


@app.command()
def chat() -> None:
    """逐轮问答补齐信息，再转入诊断与（可选）自动修复。"""
    try:
        InteractiveSession().run()
    except (typer.Abort, KeyboardInterrupt):
        console.print("\n[dim]已取消。[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()