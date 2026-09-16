"""分级授权判定（PRD §4.5.2）。

安全核心：任何写操作前都须经过级别判定；L3 高危操作（注册表 / 系统 DLL /
BIOS）永不自动执行，仅给指引。
"""

from __future__ import annotations

from typing import Protocol

from ...models import ActionLevel, RepairAction

# 高危关键词：命中即强制 L3（禁止自动）
_FORBIDDEN_KEYWORDS = (
    "registry", "regedit", "注册表", "system32", "bios", "firmware",
    "system dll", "kernel", "驱动回滚", "卸载系统组件",
)


class AuthorizationPolicy(Protocol):
    """分级授权策略协议。"""

    def classify(self, action: RepairAction) -> ActionLevel:
        """返回该动作最终级别（可能强制升级到 L3）。"""
        ...

    def can_auto(self, level: ActionLevel) -> bool:
        """L0/L1 可自动，L2 需确认，L3 禁止。"""
        ...


class DefaultAuthorizationPolicy:
    """默认策略：原语自带级别 + 高危关键词拦截。"""

    def classify(self, action: RepairAction) -> ActionLevel:
        # 高危关键词 → 强制升级为 L3（即使原语声明为 L1/L2 也不放行）。
        # 拼接 primitive + description + params 一起扫描，覆盖"动作意图"和"参数内容"。
        blob = f"{action.primitive} {action.description} {' '.join(action.params)}".lower()
        if any(kw in blob for kw in _FORBIDDEN_KEYWORDS):
            return ActionLevel.L3_FORBIDDEN
        return action.level

    def can_auto(self, level: ActionLevel) -> bool:
        # 只有只读检查（L0）与白名单安全动作（L1）可自动执行，无需逐项确认
        return level in (ActionLevel.L0_READONLY, ActionLevel.L1_SAFE)


# 全局默认策略
default_policy = DefaultAuthorizationPolicy()


def classify(action: RepairAction) -> ActionLevel:
    """便捷函数：用全局默认策略对动作做最终级别判定。"""
    return default_policy.classify(action)
