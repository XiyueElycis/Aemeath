"""角色权限矩阵（三省六部「分权制衡 / 不可越级」契约）。

协调四角色 + 执行层的调用关系是一张白名单，任何跨角色派发都必须经
:func:`can_dispatch` 校验：

```text
接待官 intake      → 规划官
规划官 planner     → 审议官
审议官 reviewer    → 规划官（封驳）/ 调度官（准奏）
调度官 dispatcher  → 已注册的领域智能体（执行层）
领域智能体 executor→ 调度官（只能回报，不能互相调用）
```

执行层智能体之间**没有横向调用权**——安全体检/存档/Mod 等任务的先后
顺序只能由调度官按计划派发，从制度上杜绝智能体互相制造假数据或越权操作。
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Set, Tuple


class Role(str, Enum):
    """协作角色。执行层通配为 ``EXECUTOR``，具体身份是智能体注册名。"""

    INTAKE = "intake"          # 接待官
    PLANNER = "planner"       # 规划官
    REVIEWER = "reviewer"     # 审议官
    DISPATCHER = "dispatcher"  # 调度官（GameAgentOrchestrator）
    EXECUTOR = "executor"     # 领域智能体（执行层）


# 协调角色之间的静态白名单（对应三省六部 openclaw.json 的 allowAgents）
_COORDINATION_MATRIX: dict[Role, frozenset[Role]] = {
    Role.INTAKE: frozenset({Role.PLANNER}),
    Role.PLANNER: frozenset({Role.REVIEWER}),
    Role.REVIEWER: frozenset({Role.PLANNER, Role.DISPATCHER}),
    Role.DISPATCHER: frozenset({Role.EXECUTOR}),
    Role.EXECUTOR: frozenset({Role.DISPATCHER}),
}

# 中文展示名（flow_log / thinking 用）
ROLE_LABELS: dict[Role, str] = {
    Role.INTAKE: "接待官",
    Role.PLANNER: "规划官",
    Role.REVIEWER: "审议官",
    Role.DISPATCHER: "调度官",
    Role.EXECUTOR: "执行智能体",
}

# 会产生写操作（创建/修改/删除/下载）的执行智能体。
# 方案模式下计划出现这些智能体即硬封驳；编辑模式放行但须遵守安全前置顺序。
MUTATING_AGENTS: frozenset[str] = frozenset({
    "installation",   # 安装链路（平台部署）
    "game_content",   # 存档写入/迁移/备份 + Mod/DLC 增删与版本同步（version 查询为只读子项，
                      # 沙箱 classify 按 op 再细分；计划层面按"可能写"保守列入）
    "script_editor",  # 文件编辑
})
# web_search 的 download 操作按任务数据动态判定，不静态列入；
# perf_security（性能/安全巡检）全程只读，不列入。

# 安全前置顺序规则：若计划包含前者（后果操作），就必须包含后者（前置保护）。
# 仅作软提示（审议 issue），硬顺序由工作流模板/编排器 requires 锁死。
# (后果智能体, 必须前置的智能体, 说明)
# 注：game_content 内部 version→saves→dlc 的顺序是同一智能体的 op 顺序，
# 由工作流任务 requires 保证，不在此跨智能体表中表达。
SAFETY_PREREQUISITES: tuple[tuple[str, str, str], ...] = (
    ("game_content", "perf_security", "装 Mod 前未做反作弊检测，有封号风险"),
)


def role_of(target: str, known_agents: Iterable[str]) -> Role:
    """判定一个调用目标属于哪个角色。

    协调角色按保留名匹配；其余已注册名一律为执行层；既不是协调角色也不是
    已注册智能体 → 抛 KeyError（调用方应视为"未知目标"拦截）。
    """
    target = target.strip()
    for role in Role:
        if target == role.value:
            return role
    if target in set(known_agents):
        return Role.EXECUTOR
    raise KeyError(target)


def can_dispatch(from_role: Role, to_target: str,
                 known_agents: Iterable[str] | None = None) -> Tuple[bool, str]:
    """校验 ``from_role`` 是否有权向目标派发任务。

    :param to_target: 协调角色保留名（planner/reviewer/dispatcher）或
        执行层智能体注册名。
    :param known_agents: 当前已注册（并启用）的智能体名集合；调度官派发时
        目标必须在此集合内。
    :return: (是否允许, 原因说明)
    """
    known: Set[str] = set(known_agents or [])
    allowed_roles = _COORDINATION_MATRIX.get(from_role, frozenset())

    # 目标是协调角色
    try:
        to_role = role_of(to_target, known)
    except KeyError:
        return False, f"目标「{to_target}」既不是协调角色也不是已注册智能体，派发被拦截"

    if to_role not in allowed_roles:
        allowed_names = "、".join(ROLE_LABELS[r] for r in allowed_roles) or "无"
        return (False,
                f"{ROLE_LABELS[from_role]}无权调用「{to_target}」（允许：{allowed_names}）")

    # 调度官 → 执行层：额外校验智能体确已注册
    if from_role == Role.DISPATCHER and to_role == Role.EXECUTOR and to_target not in known:
        return False, f"智能体「{to_target}」未注册或已停用，调度官拒绝派发"

    return True, "OK"
