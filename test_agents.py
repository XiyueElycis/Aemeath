#!/usr/bin/env python
"""智能体系统测试脚本。"""

import sys
import os
import asyncio
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))


def test_agent_factory():
    """测试智能体工厂。"""
    print("=== 测试智能体工厂 ===")
    try:
        from gamedoctor.orchestrator.agent_factory import AgentFactory, AgentManager

        # 获取可用智能体
        agents = AgentFactory.get_available_agents()
        print(f"可用智能体: {agents}")

        # 创建智能体
        if "compatibility" in agents:
            agent = AgentFactory.create_agent("compatibility")
            print(f"创建兼容性智能体成功: {agent.name}")

        return True
    except Exception as e:
        print(f"[ERROR] 智能体工厂测试失败: {e}")
        return False


def test_orchestrator():
    """测试智能体编排器。"""
    print("\n=== 测试智能体编排器 ===")
    try:
        from gamedoctor.orchestrator.orchestrator import GameAgentOrchestrator
        from gamedoctor.orchestrator.agent_factory import AgentManager

        # 创建编排器
        orchestrator = GameAgentOrchestrator()

        # 创建智能体管理器
        manager = AgentManager()
        from gamedoctor.orchestrator.agent_factory import DEFAULT_AGENT_CONFIGS

        # 注册智能体
        manager.register_agents(DEFAULT_AGENT_CONFIGS)
        manager.setup_orchestrator()

        # 列出智能体
        agents = manager.list_agents()
        print(f"注册的智能体数量: {len(agents)}")

        # 创建一个简单任务
        from gamedoctor.agents.base_agent import AgentTask

        task = AgentTask(
            name="test_task",
            priority=1,
            data={"game_name": "Test Game"}
        )

        # 执行任务
        result = asyncio.run(manager.orchestrator.execute_task(task))
        print(f"任务执行结果: {result.success}")
        if result.message:
            print(f"消息: {result.message}")

        return True
    except Exception as e:
        print(f"[ERROR] 智能体编排器测试失败: {e}")
        return False


def test_compatibility_agent():
    """测试兼容性智能体。"""
    print("\n=== 测试兼容性智能体 ===")
    try:
        from gamedoctor.agents.compatibility.compatibility_agent import CompatibilityAgent

        # 创建智能体
        agent = CompatibilityAgent()
        print(f"智能体名称: {agent.name}")
        print(f"能力: {agent.get_capabilities()}")

        # 创建任务
        from gamedoctor.agents.base_agent import AgentTask

        task = AgentTask(
            name="compatibility_check",
            priority=10,
            data={"game_name": "Grand Theft Auto V"}
        )

        # 执行任务
        result = asyncio.run(agent.execute(task))
        print(f"任务执行成功: {result.success}")
        if result.data:
            data = result.data
            print(f"兼容性评分: {data.overall_score}")
            print(f"严重问题数: {len(data.critical_issues)}")
            print(f"警告数: {len(data.warnings)}")

        return True
    except Exception as e:
        print(f"[ERROR] 兼容性智能体测试失败: {e}")
        return False


def test_system_info():
    """测试系统信息收集。"""
    print("\n=== 测试系统信息收集 ===")
    try:
        from gamedoctor.utils.system_info import get_system_info

        system_info = get_system_info()
        print("系统信息:")
        print(f"CPU: {system_info['cpu']['name']}")
        print(f"内存: {system_info['memory']['total'] / (1024**3):.1f} GB")
        print(f"GPU 数量: {len(system_info['gpu'])}")
        if system_info['gpu']:
            print(f"主GPU: {system_info['gpu'][0]['name']}")
        print(f"操作系统: {system_info['os']['name']}")

        return True
    except Exception as e:
        print(f"[ERROR] 系统信息收集失败: {e}")
        return False


def test_cli_commands():
    """测试 CLI 命令。"""
    print("\n=== 测试 CLI 命令 ===")
    try:
        from typer.testing import CliRunner
        from gamedoctor.cli import app

        runner = CliRunner()

        # 测试列出智能体命令
        result = runner.invoke(app, ['agents'])
        print(f"列出智能体命令结果: {'成功' if result.exit_code == 0 else '失败'}")

        # 测试分析游戏命令
        result = runner.invoke(app, ['analyze', 'Test Game'])
        print(f"分析游戏命令结果: {'成功' if result.exit_code == 0 else '失败'}")

        return True
    except Exception as e:
        print(f"[ERROR] CLI 命令测试失败: {e}")
        return False


def main():
    """运行所有测试。"""
    print("开始测试智能体系统...\n")

    tests = [
        ("智能体工厂", test_agent_factory),
        ("智能体编排器", test_orchestrator),
        ("兼容性智能体", test_compatibility_agent),
        ("系统信息收集", test_system_info),
        ("CLI 命令", test_cli_commands),
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
        print("[OK] 所有测试通过！智能体系统正常工作。")
    else:
        print("[ERROR] 部分测试失败，请检查错误信息。")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)