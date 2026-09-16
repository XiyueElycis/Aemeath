"""核心数据模型的基础测试。"""

from pathlib import Path

from gamedoctor.models import (
    ActionLevel,
    ErrorCategory,
    RepairAction,
    RepairPlan,
    to_dict,
)


def test_to_dict_serializes_nested():
    """验证嵌套模型（计划包含动作）能递归序列化，且枚举转字符串。"""
    plan = RepairPlan(
        diagnosis_id="d1",
        category=ErrorCategory.RENDER_ERROR,
        actions=[RepairAction(id="a1", primitive="append_launch_arg", level=ActionLevel.L1_SAFE)],
    )
    d = to_dict(plan)
    # 枚举类别序列化为 snake_case 字符串
    assert d["category"] == "render_error"
    # 动作级别序列化为短标签
    assert d["actions"][0]["level"] == "L1"
    # 原语名称原样保留
    assert d["actions"][0]["primitive"] == "append_launch_arg"


def test_sorted_actions_by_priority():
    """验证动作按优先级排序：数值越小越靠前。"""
    a1 = RepairAction(id="low", primitive="x", priority=10)
    a2 = RepairAction(id="high", primitive="y", priority=0)
    plan = RepairPlan(actions=[a1, a2])
    # 优先级 0 的高优先级动作应排在优先级 10 之前
    assert [a.id for a in plan.sorted_actions()] == ["high", "low"]


def test_path_serialization():
    """验证 Path 对象能被序列化为字符串路径。"""
    d = to_dict({"p": Path("a/b/c")})
    # Path 应转成平台化的字符串表示
    assert d["p"] == str(Path("a/b/c"))
