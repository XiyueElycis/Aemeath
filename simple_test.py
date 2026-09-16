#!/usr/bin/env python
"""简单的智能体系统测试。"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径 
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

def test_imports():
    """测试导入。"""
    print("=== 测试导入 ===")
    try:
        # 测试基础导入
        from gamedoctor.agents.base_agent import BaseAgent, AgentTask, AgentResult
        print("[OK] 基础智能体类导入成功")

        # 测试编排器
        from gamedoctor.orchestrator.orchestrator import GameAgentOrchestrator
        print("[OK] 编排器导入成功")

        # 测试智能体工厂
        from gamedoctor.orchestrator.agent_factory import AgentFactory
        print("[OK] 智能体工厂导入成功")

        # 测试模型
        from gamedoctor.models import GameContext
        print("[OK] GameContext 导入成功")

        return True
    except Exception as e:
        print(f"[ERROR] 导入失败: {e}")
        return False

def test_basic_functionality():
    """测试基本功能。"""
    print("\n=== 测试基本功能 ===")
    try:
        # 测试 AgentTask 创建
        from gamedoctor.agents.base_agent import AgentTask

        task = AgentTask(
            name="test_task",
            priority=1,
            data={"game_name": "Test Game"}
        )
        print(f"[OK] 创建任务: {task.name}")

        # 测试 AgentResult 创建
        from gamedoctor.agents.base_agent import AgentResult

        result = AgentResult(
            success=True,
            data={"test": "data"},
            message="Test successful"
        )
        print(f"[OK] 创建结果: {result.success}")

        # 测试 AgentFactory
        from gamedoctor.orchestrator.agent_factory import AgentFactory

        available_agents = AgentFactory.get_available_agents()
        print(f"[OK] 可用智能体: {available_agents}")

        return True
    except Exception as e:
        print(f"[ERROR] 基本功能测试失败: {e}")
        return False

def test_cli():
    """测试 CLI。"""
    print("\n=== 测试 CLI ===")
    try:
        from typer.testing import CliRunner
        from gamedoctor.cli import app

        runner = CliRunner()

        # 测试 --help
        result = runner.invoke(app, ['--help'])
        if result.exit_code == 0:
            print("[OK] CLI --help 命令成功")
        else:
            print("[ERROR] CLI --help 命令失败")
            return False

        return True
    except Exception as e:
        print(f"[ERROR] CLI 测试失败: {e}")
        return False

def main():
    """运行测试。"""
    print("开始简单测试...\n")

    tests = [
        ("导入测试", test_imports),
        ("基本功能测试", test_basic_functionality),
        ("CLI 测试", test_cli),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"测试 {test_name}...")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"[ERROR] {test_name} 测试异常: {e}")
            results.append((test_name, False))

    # 输出测试结果
    print("\n" + "="*50)
    print("测试结果总结:")
    passed = 0
    total = len(results)

    for test_name, result in results:
        status = "[OK]" if result else "[ERROR]"
        print(f"{status} {test_name}")
        if result:
            passed += 1

    print(f"\n通过: {passed}/{total}")

    if passed == total:
        print("[OK] 所有测试通过！")
    else:
        print("[ERROR] 部分测试失败，请检查错误信息。")

    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)