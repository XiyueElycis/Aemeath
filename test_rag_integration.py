#!/usr/bin/env python
"""测试 RAG 集成功能。"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

def test_config():
    """测试配置加载。"""
    print("=== 测试配置加载 ===")
    try:
        from gamedoctor.config import load
        cfg = load()

        print(f"[OK] 配置加载成功")
        print(f"  use_vector: {cfg.get('knowledge.use_vector')}")
        print(f"  auto_index: {cfg.get('knowledge.auto_index')}")
        print(f"  rag_data_dir: {cfg.get('knowledge.rag_data_dir')}")
        return True
    except Exception as e:
        print(f"[ERROR] 配置加载失败: {e}")
        return False

def test_dependency_check():
    """测试依赖检查。"""
    print("\n=== 测试依赖检查 ===")
    try:
        from gamedoctor.utils.dependency_check import DependencyChecker

        deps = DependencyChecker.check_vector_dependencies()
        print(f"[OK] 依赖检查成功")
        for dep, available in deps.items():
            status = "[OK]" if available else "[ERROR]"
            print(f"  {status} {dep}")

        # 测试依赖检查命令
        from gamedoctor.cli import config_app
        return True
    except Exception as e:
        print(f"[ERROR] 依赖检查失败: {e}")
        return False

def test_hybrid_retriever():
    """测试混合检索器。"""
    print("\n=== 测试混合检索器 ===")
    try:
        from gamedoctor.knowledge.retriever import HybridRetriever
        from gamedoctor.config import load

        cfg = load()
        retriever = HybridRetriever(cfg)

        # 测试搜索（应该回退到 SQLite）
        results = retriever.search("0xc000007b")
        print(f"[OK] 混合检索器工作正常")
        print(f"  检索到 {len(results)} 个结果")

        # 测试自动索引功能
        print("\n测试自动索引...")
        indexed_games = retriever._auto_index_games()
        print(f"  自动索引结果: {indexed_games}")

        return True
    except Exception as e:
        print(f"[ERROR] 混合检索器失败: {e}")
        return False

def test_pipeline():
    """测试诊断流水线。"""
    print("\n=== 测试诊断流水线 ===")
    try:
        from gamedoctor.pipeline import DiagnosisPipeline

        pipeline = DiagnosisPipeline()
        print("[OK] 诊断流水线创建成功")

        # 测试 _gather_context
        from gamedoctor.models import TechStackFingerprint, ErrorReport, ErrorCategory

        # 创建测试数据
        fp = TechStackFingerprint(game_name="Test Game")
        error = ErrorReport(category=ErrorCategory.LAUNCH_FAILURE, raw_message="0xc000007b")

        ctx = pipeline._gather_context(fp, error)
        print(f"[OK] 上下文收集成功")
        print(f"  搜索结果: {len(ctx.searches)}")
        print(f"  知识结果: {len(ctx.knowledge)}")
        print(f"  日志结果: {len(ctx.logs)}")

        return True
    except Exception as e:
        print(f"[ERROR] 诊断流水线失败: {e}")
        return False

def main():
    """运行所有测试。"""
    print("开始测试 RAG 集成...\n")

    tests = [
        test_config,
        test_dependency_check,
        test_hybrid_retriever,
        test_pipeline,
    ]

    results = []
    for test in tests:
        results.append(test())

    print("\n" + "="*50)
    print("测试结果总结:")
    passed = sum(results)
    total = len(results)
    print(f"通过: {passed}/{total}")

    if passed == total:
        print("[OK] 所有测试通过！RAG 集成功能正常。")
    else:
        print("[ERROR] 部分测试失败，请检查错误信息。")

    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)