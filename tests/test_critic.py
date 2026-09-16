"""StructuralCritic 单测 — 规则校验动作合法性。

要点：
- 未知原语 → 剔除并记 issue
- 缺必填参数 → 剔除
- edit_config 路径二选一（config_path 或 path）可通过
- 非法 level 记 issue 不剔除
- L3 标记软提示不剔除
"""

from __future__ import annotations

import pytest

from gamedoctor.models import ActionLevel, RepairAction, RepairPlan, ReviewResult
from gamedoctor.solver.critic import StructuralCritic


def test_unknown_primitive():
    """未知原语被剔除并记录 issue。"""
    critic = StructuralCritic()
    plan = RepairPlan(actions=[
        RepairAction(
            id="a1",
            primitive="unknown_primitive",
            params={},
            level=ActionLevel.L1_SAFE
        )
    ])

    review = critic.review(plan)
    assert not review.passed
    assert "动作 a1: 未知原语 'unknown_primitive'" in review.issues
    assert "a1" in review.bad_action_ids
    # 检查过滤后的动作列表
    filtered_actions = [a for a in plan.actions if a.id not in review.bad_action_ids]
    assert len(filtered_actions) == 0  # 剔除


def test_missing_required_params():
    """缺必填参数被剔除。"""
    critic = StructuralCritic()
    plan = RepairPlan(actions=[
        RepairAction(
            id="a1",
            primitive="edit_config",
            params={},  # 缺少 key
            level=ActionLevel.L1_SAFE
        )
    ])

    review = critic.review(plan)
    assert not review.passed
    assert "动作 a1: 原语 edit_config 需确认，级别 L1 偏低" in review.issues
    assert "a1" in review.bad_action_ids
    # 检查过滤后的动作列表
    filtered_actions = [a for a in plan.actions if a.id not in review.bad_action_ids]
    assert len(filtered_actions) == 0  # 剔除


def test_edit_config_path_alt():
    """edit_config 的 config_path/path 二选一可通过。"""
    critic = StructuralCritic()
    plan = RepairPlan(actions=[
        # 有 config_path
        RepairAction(
            id="a1",
            primitive="edit_config",
            params={"config_path": "/test.ini", "key": "test"},
            level=ActionLevel.L1_SAFE
        ),
        # 有 path
        RepairAction(
            id="a2",
            primitive="edit_config",
            params={"path": "C:/test.ini", "key": "test"},
            level=ActionLevel.L1_SAFE
        )
    ])

    review = critic.review(plan)
    assert review.passed  # 都通过
    assert len(review.bad_action_ids) == 0


def test_invalid_level():
    """非法 level 记 issue 不剔除。"""
    critic = StructuralCritic()
    plan = RepairPlan(actions=[
        RepairAction(
            id="a1",
            primitive="edit_config",
            params={"key": "test"},
            level="invalid_level"  # 不是 ActionLevel 枚举
        )
    ])

    review = critic.review(plan)
    assert not review.passed
    assert "动作 a1: 级别非法 'invalid_level'" in review.issues
    assert "a1" in review.bad_action_ids
    # 检查过滤后的动作列表
    filtered_actions = [a for a in plan.actions if a.id not in review.bad_action_ids]
    assert len(filtered_actions) == 0  # 剔除


def test_l3_forbidden_soft_warning():
    """L3 动作只记软提示不剔除。"""
    critic = StructuralCritic()
    plan = RepairPlan(actions=[
        RepairAction(
            id="a1",
            primitive="edit_config",
            params={"key": "test"},
            level=ActionLevel.L3_FORBIDDEN
        )
    ])

    review = critic.review(plan)
    assert review.passed  # 不剔除
    assert "动作 a1: L3 动作仅作指引，不会自动执行" in review.issues
    assert len(review.bad_action_ids) == 0
    assert len(plan.actions) == 1  # 保留


def test_confirm_level_low_warning():
    """需确认原语被标为 L0/L1 记警告。"""
    critic = StructuralCritic()
    plan = RepairPlan(actions=[
        RepairAction(
            id="a1",
            primitive="edit_config",  # 默认 L2
            params={"key": "test"},
            level=ActionLevel.L0_READONLY  # 偏低
        )
    ])

    review = critic.review(plan)
    assert review.passed  # 不剔除
    assert "动作 a1: 原语 edit_config 需确认，级别 L0 偏低" in review.issues
    assert len(review.bad_action_ids) == 0


def test_mixed_issues():
    """混合多种 issue。"""
    critic = StructuralCritic()
    plan = RepairPlan(actions=[
        # 剔除项
        RepairAction("a1", "unknown", {}, ActionLevel.L1_SAFE),
        RepairAction("a2", "edit_config", {}, ActionLevel.L1_SAFE),  # 缺 key
        # 保留项但记警告
        RepairAction("a3", "edit_config", {"key": "test"}, ActionLevel.L0_READONLY),  # L0 警告
        RepairAction("a4", "edit_config", {"key": "test"}, ActionLevel.L3_FORBIDDEN),  # L3 警告
    ])

    review = critic.review(plan)
    assert not review.passed
    assert len(review.bad_action_ids) == 2  # a1, a2 剔除
    filtered_actions = [a for a in plan.actions if a.id not in review.bad_action_ids]
    assert len(filtered_actions) == 2  # a3, a4 保留
    assert "动作 a1: 未知原语 'unknown'" in review.issues
    assert "动作 a2: 缺少必填参数 key" in review.issues
    assert "动作 a3: 原语 edit_config 需确认，级别 L0 偏低" in review.issues
    assert "动作 a4: L3 动作仅作指引，不会自动执行" in review.issues