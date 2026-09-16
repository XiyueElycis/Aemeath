"""诊断报告渲染（PRD §5 输出报告格式）。

用 Rich 渲染 :class:`~gamedoctor.models.DiagnosisReport`，包括技术栈、
根因判断、修复方案（按优先级 + 级别）、自动修复执行结果、风险提示与参考资料。

注意：所有动态内容（用户错误信息 / 模型输出 / URL / 枚举值）一律经
:func:`rich.markup.escape` 转义，避免被 Rich 当作 markup 样式标签解析。
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .models import ActionLevel, DiagnosisReport, FixStatus

console = Console()


def _level_style(level: ActionLevel) -> str:
    """返回动作级别对应的 Rich 样式名（用于渲染着色）。"""
    return {
        ActionLevel.L0_READONLY: "dim",
        ActionLevel.L1_SAFE: "green",
        ActionLevel.L2_CONFIRM: "yellow",
        ActionLevel.L3_FORBIDDEN: "red",
    }.get(level, "white")


def render(report: DiagnosisReport) -> None:
    """打印诊断报告。"""
    ts = report.tech_stack
    engine = f"{ts.engine.engine} {ts.engine.version or ''}".strip() if ts.engine else "未知"
    header = (
        f"🔍 诊断报告  #{report.diagnosis_id}\n"
        f"技术栈: {escape(engine)} | {escape(ts.runtime.os)} | "
        f"{escape(ts.platform.platform)} | {escape(ts.runtime.gpu or 'GPU未知')}"
    )
    console.print(Panel(header, border_style="cyan"))

    # 根因判断
    if report.error and report.error.category:
        cat = escape(f"[{report.error.category.value}]")
        body = (
            f"{cat} 置信度: {report.error.confidence.value} | "
            f"来源: {report.error.source.value}\n"
            f"{escape(report.error.raw_message or '(无错误信息)')}"
        )
        console.print(Panel(body, title="🎯 根因判断", border_style="yellow"))

    # 修复方案
    if report.plan and report.plan.actions:
        table = Table(title="🛠️ 修复方案（按优先级）", show_lines=True)
        table.add_column("#", justify="right")
        table.add_column("动作", style="bold")
        table.add_column("级别")
        table.add_column("验证")
        for i, a in enumerate(report.plan.sorted_actions(), 1):
            table.add_row(
                str(i),
                escape(a.description),
                f"[{_level_style(a.level)}]{a.level.value}[/]",
                escape(a.verify_checkpoint or "-"),
            )
        console.print(table)
    else:
        console.print("[dim]（暂无修复方案 —— 方案生成层尚未实现）[/]")

    # 自动修复执行结果
    if report.execution:
        table = Table(title="⚙️ 自动修复执行结果", show_lines=True)
        table.add_column("动作")
        table.add_column("结果")
        for r in report.execution.results:
            icon = {
                FixStatus.SUCCESS: "✅",
                FixStatus.FAILED: "❌",
                FixStatus.SKIPPED: "⏭️",
                FixStatus.ROLLED_BACK: "↩️",
                FixStatus.NEEDS_MANUAL: "🧑‍🔧",
                FixStatus.PENDING: "…",
            }.get(r.status, "·")
            table.add_row(escape(r.message), icon)
        summary = (f"成功 {report.execution.succeeded} 项，失败 {report.execution.failed} 项"
                   + ("，[red]已触发回滚[/]" if report.execution.rolled_back else ""))
        console.print(table)
        console.print(summary)

    # 风险提示 / 参考资料
    if report.risks:
        console.print(Panel(
            "\n".join(f"- {escape(r)}" for r in report.risks),
            title="⚠️ 风险提示", border_style="red",
        ))
    if report.references:
        refs = "\n".join(
            f"- {escape(r.source)} | {escape(r.title)} | {escape(r.url)}"
            for r in report.references[:5]
        )
        console.print(Panel(refs, title="🔗 参考资料", border_style="blue"))
