"""分级授权策略测试。"""

from gamedoctor.fixer.policy.levels import DefaultAuthorizationPolicy
from gamedoctor.models import ActionLevel, RepairAction


def test_l1_stays_auto():
    """验证 L1 安全动作保持原级别，且允许自动执行。"""
    policy = DefaultAuthorizationPolicy()
    action = RepairAction(id="a1", primitive="append_launch_arg", level=ActionLevel.L1_SAFE)
    # L1 动作分类后仍是 L1
    assert policy.classify(action) == ActionLevel.L1_SAFE
    # L1 属于可自动执行级别
    assert policy.can_auto(action.level)


def test_registry_keyword_forced_l3():
    """验证含"注册表"类危险关键词的动作被强制升级为 L3 禁止级别。"""
    policy = DefaultAuthorizationPolicy()
    action = RepairAction(
        id="a2", primitive="edit_registry", level=ActionLevel.L2_CONFIRM,
        description="修改注册表键值",
    )
    # 即便声明为 L2，也会因关键词被强制归类为 L3
    assert policy.classify(action) == ActionLevel.L3_FORBIDDEN
    # L3 禁止自动执行
    assert not policy.can_auto(ActionLevel.L3_FORBIDDEN)
