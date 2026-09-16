"""交叉自检（PRD §4.6 方案自检）。

两层自检：
- :class:`StructuralCritic`（P0）：规则校验动作合法性（原语已注册、级别合法、必填参数
  齐全），防止 LLM 幻觉产出无效原语/缺参动作被送进执行器。
- :class:`LLMCritic`（P2 占位）：用与生成不同的模型做语义交叉审查，尚未接 LLM。
"""

from __future__ import annotations

from typing import Protocol

from ..models import ActionLevel, RepairPlan, ReviewResult

# 导入即触发 @register，保证下方 REGISTRY 已填充
import gamedoctor.fixer.primitives  # noqa: F401
from ..fixer.primitives.base import REGISTRY  # noqa: E402

# 必填参数表（与 generator._PARAM_HINTS.required 对齐）
_REQUIRED: dict[str, list[str]] = {
    "quarantine_file": ["path"],
    "clean_cache": ["path"],
    "append_launch_arg": ["config_path", "args"],
    "edit_config": ["key"],
    "replace_text": ["path", "old"],
    "install_runtime": ["component"],
    "steam_verify_integrity": [],
    "epic_repair": [],
}
# edit_config 的路径参数二选一：config_path 或 path
_PATH_ALTS: dict[str, tuple[str, ...]] = {
    "edit_config": ("config_path", "path"),
}


class Critic(Protocol):
    """方案自检器协议。"""

    def review(self, plan: RepairPlan) -> ReviewResult:
        """审查修复计划，返回自检结论。"""
        ...


class StructuralCritic:
    """结构性自检（规则驱动，不调 LLM）。

    硬失败（动作被剔除）：未知原语 / 级别非法 / 必填参数缺失。
    软提示（动作保留，仅记 issue）：原语需确认却被标低级别、L3 仅指引。
    """

    name = "structural"

    def review(self, plan: RepairPlan) -> ReviewResult:
        issues: list[str] = []
        bad: list[str] = []
        names = set(REGISTRY.keys())

        for a in plan.actions:
            bid = a.id
            if a.primitive not in names:
                issues.append(f"动作 {bid}: 未知原语 '{a.primitive}'")
                bad.append(bid)
                continue
            # 级别必须是合法 ActionLevel
            if not isinstance(a.level, ActionLevel):
                issues.append(f"动作 {bid}: 级别非法 '{a.level}'")
                bad.append(bid)
                continue
            inst = REGISTRY[a.primitive]
            # 软提示：原语默认 L2（需确认）却被标为 L0/L1（试图绕过确认）
            if inst.default_level == ActionLevel.L2_CONFIRM and a.level in (
                ActionLevel.L0_READONLY, ActionLevel.L1_SAFE,
            ):
                issues.append(
                    f"动作 {bid}: 原语 {a.primitive} 需确认，级别 {a.level.value} 偏低"
                )
            # 硬失败：必填参数缺失
            required = _REQUIRED.get(a.primitive, [])
            for p in required:
                if a.params.get(p):
                    continue
                # edit_config 路径可用 config_path 或 path 任一
                alts = _PATH_ALTS.get(a.primitive)
                if alts and any(a.params.get(x) for x in alts):
                    continue
                issues.append(f"动作 {bid}: 缺少必填参数 {p}")
                bad.append(bid)
                break
            # 软提示：L3 仅作指引，不会自动执行
            if a.level == ActionLevel.L3_FORBIDDEN:
                issues.append(f"动作 {bid}: L3 动作仅作指引，不会自动执行")

        return ReviewResult(
            passed=not bad,
            issues=issues,
            bad_action_ids=bad,
            verdict="结构校验通过" if not bad else f"{len(bad)} 项结构问题",
        )


class LLMCritic:
    """基于 LLM 的交叉审查骨架（P2，尚未接 LLM）。

    理想实现：组装计划摘要（动作 + 级别 + 验证点）→
    ``router.complete(Task.CRITIC, prompt)`` → 解析 {passed, issues, verdict}。
    """

    def __init__(self, router=None):
        # 默认构造一个模型路由器；自检通常应配置与生成不同的模型
        self.router = router

    def review(self, plan: RepairPlan) -> ReviewResult:
        """对修复计划做交叉审查并返回结论。

        P2 待实现：调 LLM 做语义审查。当前默认放行，仅靠 StructuralCriter 兜底。
        """
        # TODO(P2): 组装计划摘要 → router.complete(Task.CRITIC, prompt) → 解析结论
        return ReviewResult(passed=True, verdict="未执行 LLM 交叉自检（P2）")
