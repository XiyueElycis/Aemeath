"""审议官（门下省角色）：规划官的方案必须经此强制审议，不可跳过。

纯规则驱动（0 次 LLM 调用），保证审议结论确定性、可复现：

**硬伤（blocking）→ 封驳**，计划退回规划官并附问题清单；连续
:data:`~gamedoctor.governance.state_machine.MAX_REVIEW_ROUNDS` 轮仍有
硬伤则终止交付——硬伤绝不"强制通过"（安全红线不可被轮次覆盖）：

1. 空计划（无阶段 / 阶段无任务）；
2. 任务引用未注册或已停用的智能体；
3. ``requires`` 引用了计划内不存在的任务名；
4. ``dependencies`` 引用了不存在的阶段名；
5. 阶段依赖或任务依赖构成环（DAG 不成立）；
6. 方案模式出现写操作智能体或 download 任务。

**软问题（warnings）→ 不封驳**，随准奏结论带给调度官与回奏：
安全前置缺失（装 Mod 前无反作弊/版本检测）、optional 任务被必选任务
依赖等。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from ..orchestrator.orchestrator import OrchestrationPlan
from .permissions import MUTATING_AGENTS, SAFETY_PREREQUISITES


@dataclass
class ReviewVerdict:
    """审议结论。``blocking`` 非空即封驳。"""

    blocking: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return not self.blocking


class Reviewer:
    """审议官：结构合法性 + 安全合规强制门。"""

    def review(self, plan: OrchestrationPlan, *, mode: str,
               known_agents: Set[str]) -> ReviewVerdict:
        """审议一份计划。

        :param known_agents: 当前已注册且启用的智能体名集合（调度白名单）。
        """
        v = ReviewVerdict()

        # 1. 空计划
        if plan is None or not getattr(plan, "stages", None):
            v.blocking.append("计划为空：没有任何可执行阶段")
            v.suggestions.append("请基于用户需求至少规划一个调查类阶段")
            return v

        stages = plan.stages
        stage_names = {s.name for s in stages}
        task_owner: Dict[str, str] = {}      # 任务名 -> 所属阶段
        task_meta: Dict[str, Tuple[str, bool]] = {}  # 任务名 -> (agent, optional)
        agents_used: Set[str] = set()

        for st in stages:
            if not st.tasks:
                v.blocking.append(f"阶段「{st.name}」没有任何任务")
                continue
            for t in st.tasks:
                if t.name in task_owner:
                    v.blocking.append(f"任务名「{t.name}」重复（阶段 {task_owner[t.name]} 与 {st.name}）")
                    continue
                task_owner[t.name] = st.name
                task_meta[t.name] = (t.agent or "", bool(getattr(t, "optional", False)))
                if t.agent:
                    agents_used.add(t.agent)

        # 2. 未知/停用智能体
        for tname, (agent, _opt) in task_meta.items():
            if not agent:
                v.blocking.append(f"任务「{tname}」缺少 agent 字段")
            elif agent not in known_agents:
                v.blocking.append(
                    f"任务「{tname}」引用了未注册或已停用的智能体「{agent}」"
                )
                v.suggestions.append(f"请把「{tname}」改派给目录中存在的智能体，或删除该任务")

        # 3. requires 引用合法性 + 4. 任务依赖环
        task_edges: Dict[str, List[str]] = {name: [] for name in task_owner}
        for st in stages:
            for t in st.tasks:
                for req in (t.requires or []):
                    if req not in task_owner:
                        v.blocking.append(
                            f"任务「{t.name}」requires 了计划内不存在的任务「{req}」"
                        )
                    elif req == t.name:
                        v.blocking.append(f"任务「{t.name}」不能依赖自身")
                    else:
                        task_edges[t.name].append(req)
        self._check_cycle(task_edges, kind="任务", verdict=v)

        # 5. 阶段 dependencies 合法性 + 环
        stage_edges: Dict[str, List[str]] = {name: [] for name in stage_names}
        for st in stages:
            for dep in (st.dependencies or []):
                if dep not in stage_names:
                    v.blocking.append(
                        f"阶段「{st.name}」dependencies 了不存在的阶段「{dep}」"
                    )
                elif dep == st.name:
                    v.blocking.append(f"阶段「{st.name}」不能依赖自身")
                else:
                    stage_edges[st.name].append(dep)
        self._check_cycle(stage_edges, kind="阶段", verdict=v)

        # 6. 方案模式安全红线
        if mode == "plan":
            for tname, (agent, _opt) in task_meta.items():
                if agent in MUTATING_AGENTS:
                    v.blocking.append(
                        f"方案模式禁止写操作：任务「{tname}」使用了 {agent}"
                    )
            for st in stages:
                for t in st.tasks:
                    op = str((t.data or {}).get("operation", "")).lower()
                    if op == "download" or (t.agent == "web_search" and op == "download"):
                        v.blocking.append(f"方案模式禁止下载：任务「{t.name}」")
            if any("方案模式" in b for b in v.blocking):
                v.suggestions.append("请改为只读调查计划（日志/兼容性/性能/网络/安全检测）")

        # 7. 软问题：安全前置顺序
        for doer, prerequisite, why in SAFETY_PREREQUISITES:
            if doer in agents_used and prerequisite not in agents_used:
                v.warnings.append(f"计划包含 {doer} 但缺少 {prerequisite}：{why}")

        # 8. 软问题：必选任务依赖 optional 任务（前置一跳过它必跳过）
        optional_names = {n for n, (_a, opt) in task_meta.items() if opt}
        for st in stages:
            for t in st.tasks:
                if getattr(t, "optional", False):
                    continue
                bad = [r for r in (t.requires or []) if r in optional_names]
                for r in bad:
                    v.warnings.append(
                        f"必选任务「{t.name}」依赖了可选任务「{r}」，"
                        f"前者失败被跳过时后者将连带跳过"
                    )

        return v

    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_cycle(edges: Dict[str, List[str]], *, kind: str,
                     verdict: ReviewVerdict) -> None:
        """DFS 检测有向图环（白/灰/黑着色）。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in edges}

        def visit(node: str, stack: List[str]) -> None:
            color[node] = GRAY
            stack.append(node)
            for nxt in edges.get(node, []):
                if nxt not in color:
                    continue  # 引用不存在已在前面记 blocking
                if color[nxt] == GRAY:
                    cycle = " → ".join(stack[stack.index(nxt):] + [nxt])
                    verdict.blocking.append(f"{kind}依赖存在环：{cycle}")
                    return
                if color[nxt] == WHITE:
                    visit(nxt, stack)
            stack.pop()
            color[node] = BLACK

        for n in list(edges):
            if color[n] == WHITE:
                visit(n, [])

    # ------------------------------------------------------------------ #
    def review_outcome(self, outcome: Dict[str, Any]) -> ReviewVerdict:
        """执行后验收复审：判定结构化结果是否可交付。

        执行层失败不是计划缺陷，审议官不要求重规划（避免故障放大），
        只出具「带保留通过」结论，由回奏如实告知用户失败项。
        """
        v = ReviewVerdict()
        counts = (outcome or {}).get("counts", {})
        failed = counts.get("failed", 0)
        skipped = counts.get("skipped", 0)
        if failed:
            v.warnings.append(f"{failed} 个必选任务执行失败，交付时须如实列出失败环节")
        if skipped:
            v.warnings.append(f"{skipped} 个任务因前置失败被门控跳过")
        return v
