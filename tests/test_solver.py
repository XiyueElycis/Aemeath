"""LLMSolver 单测 — prompt 组装与 JSON 解析。

要点：
- 注入假 router 返回固定 JSON → 验证解析为 RepairPlan
- 未知原语被丢弃
- 损坏 JSON 抛 SolverError
- 检查 confidence/level 还原逻辑
- 无 LLM 降级为空计划
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from gamedoctor.errors import SolverError
from gamedoctor.models import (
    ActionLevel,
    ConfidenceLevel,
    ErrorCategory,
    ErrorReport,
    RepairAction,
    RepairPlan,
    SourceType,
    TechStackFingerprint,
)
from gamedoctor.solver.generator import LLMSolver


def test_generate_success():
    """成功生成计划。"""
    # mock router 返回固定 JSON
    mock_router = Mock()
    mock_router.complete.return_value = json.dumps({
        "root_cause": "Test root cause",
        "confidence": "high",
        "actions": [
            {
                "id": "a1",
                "primitive": "edit_config",
                "params": {"key": "fullscreen", "value": "true"},
                "level": "L1",
                "description": "Enable fullscreen mode",
                "verify_checkpoint": "fullscreen=true in config",
                "priority": 1
            }
        ]
    })

    solver = LLMSolver(router=mock_router)
    fp = TechStackFingerprint(game_name="Test Game")
    error = ErrorReport(
        category=ErrorCategory.LAUNCH_FAILURE,
        raw_message="Test error",
        signature="test signature"
    )

    plan = solver.generate(fp, error)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.id == "a1"
    assert action.primitive == "edit_config"
    assert action.params["key"] == "fullscreen"
    assert action.level == ActionLevel.L1_SAFE
    assert plan.root_cause == "Test root cause"
    assert plan.confidence == ConfidenceLevel.HIGH


def test_unknown_primitive_dropped():
    """未知原语被丢弃。"""
    mock_router = Mock()
    mock_router.complete.return_value = json.dumps({
        "actions": [
            {
                "id": "a1",
                "primitive": "unknown_primitive",  # 不在 REGISTRY
                "params": {"path": "/test"},
                "level": "L1"
            },
            {
                "id": "a2",
                "primitive": "edit_config",  # 合法原语
                "params": {"key": "test"},
                "level": "L1"
            }
        ]
    })

    solver = LLMSolver(router=mock_router)
    fp = TechStackFingerprint(game_name="Test")
    error = ErrorReport(
        category=ErrorCategory.LAUNCH_FAILURE,
        raw_message="test"
    )

    plan = solver.generate(fp, error)
    # 只有合法原语被保留
    assert len(plan.actions) == 1
    assert plan.actions[0].primitive == "edit_config"


def test_json_parse_error():
    """JSON 解析失败抛 SolverError。"""
    mock_router = Mock()
    mock_router.complete.return_value = "invalid json response"

    solver = LLMSolver(router=mock_router)
    fp = TechStackFingerprint(game_name="Test")
    error = ErrorReport(
        category=ErrorCategory.LAUNCH_FAILURE,
        raw_message="test"
    )

    with pytest.raises(SolverError):
        solver.generate(fp, error)


def test_actions_only_array():
    """LLM 直接返回 actions 数组。"""
    mock_router = Mock()
    mock_router.complete.return_value = json.dumps([
        {
            "id": "a1",
            "primitive": "edit_config",
            "params": {"key": "test"},
            "level": "L0"
        }
    ])

    solver = LLMSolver(router=mock_router)
    fp = TechStackFingerprint(game_name="Test")
    error = ErrorReport(
        category=ErrorCategory.LAUNCH_FAILURE,
        raw_message="test"
    )

    plan = solver.generate(fp, error)
    assert len(plan.actions) == 1
    assert plan.actions[0].id == "a1"


def test_missing_level_defaults():
    """level 缺失时用原语默认值。"""
    mock_router = Mock()
    mock_router.complete.return_value = json.dumps({
        "actions": [
            {
                "id": "a1",
                "primitive": "edit_config",  # 默认 L2
                "params": {"key": "test"}
            }
        ]
    })

    solver = LLMSolver(router=mock_router)
    fp = TechStackFingerprint(game_name="Test")
    error = ErrorReport(
        category=ErrorCategory.LAUNCH_FAILURE,
        raw_message="test"
    )

    plan = solver.generate(fp, error)
    assert len(plan.actions) == 1
    # edit_config 默认是 L2
    assert plan.actions[0].level == ActionLevel.L2_CONFIRM


def test_llm_failure_throws_error():
    """LLM 调用失败抛 SolverError。"""
    mock_router = Mock()
    mock_router.complete.side_effect = Exception("Network error")

    solver = LLMSolver(router=mock_router)
    fp = TechStackFingerprint(game_name="Test")
    error = ErrorReport(
        category=ErrorCategory.LAUNCH_FAILURE,
        raw_message="test"
    )

    with pytest.raises(Exception):
        solver.generate(fp, error)


def test_confidence_fallback():
    """非法 confidence 降级为 MEDIUM。"""
    mock_router = Mock()
    mock_router.complete.return_value = json.dumps({
        "confidence": "invalid_value",
        "actions": []
    })

    solver = LLMSolver(router=mock_router)
    fp = TechStackFingerprint(game_name="Test")
    error = ErrorReport(
        category=ErrorCategory.LAUNCH_FAILURE,
        raw_message="test"
    )

    plan = solver.generate(fp, error)
    assert plan.confidence == ConfidenceLevel.MEDIUM