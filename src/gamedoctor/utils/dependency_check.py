"""依赖检查工具。

检查并管理项目依赖，特别是可选的向量数据库依赖。
"""

from __future__ import annotations

import sys
from typing import List, Dict, Optional

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False


class DependencyChecker:
    """依赖检查器。"""

    @staticmethod
    def check_vector_dependencies() -> Dict[str, bool]:
        """检查向量数据库相关依赖。"""
        return {
            "chromadb": CHROMADB_AVAILABLE,
            "sentence-transformers": DependencyChecker._check_module("sentence_transformers"),
            "torch": DependencyChecker._check_module("torch"),
        }

    @staticmethod
    def check_llm_dependencies() -> Dict[str, bool]:
        """检查 LLM 相关依赖。"""
        return {
            "httpx": DependencyChecker._check_module("httpx"),
            "langchain": DependencyChecker._check_module("langchain"),
            "langchain_deepseek": DependencyChecker._check_module("langchain_deepseek"),
        }

    @staticmethod
    def _check_module(module_name: str) -> bool:
        """检查模块是否可用。"""
        try:
            __import__(module_name)
            return True
        except ImportError:
            return False

    @staticmethod
    def get_missing_dependencies(deps: Dict[str, bool]) -> List[str]:
        """获取缺失的依赖列表。"""
        return [dep for dep, available in deps.items() if not available]

    @staticmethod
    def suggest_installation(deps: Dict[str, bool]) -> str:
        """生成安装建议。"""
        missing = DependencyChecker.get_missing_dependencies(deps)
        if not missing:
            return "所有依赖都已安装。"

        if "chromadb" in missing:
            suggestion = """缺少向量数据库依赖，请安装：
pip install 'gamedoctor[vector]'
或者：
pip install chromadb>=0.4
"""
        else:
            suggestion = f"缺少以下依赖：\n"
            for dep in missing:
                suggestion += f"pip install {dep}\n"

        return suggestion

    @staticmethod
    def check_vector_availability(config=None) -> bool:
        """检查向量检索是否可用。"""
        if not CHROMADB_AVAILABLE:
            return False

        if config:
            # 检查配置是否启用向量检索
            return config.get("knowledge.use_vector", False)

        return True

    @staticmethod
    def print_dependency_status():
        """打印依赖状态。"""
        vector_deps = DependencyChecker.check_vector_dependencies()
        llm_deps = DependencyChecker.check_llm_dependencies()

        print("=== 依赖检查结果 ===")
        print("\n[向量数据库依赖]")
        for dep, available in vector_deps.items():
            status = "✓" if available else "✗"
            print(f"  {status} {dep}")

        print("\n[LLM 相关依赖]")
        for dep, available in llm_deps.items():
            status = "✓" if available else "✗"
            print(f"  {status} {dep}")

        missing = DependencyChecker.get_missing_dependencies(vector_deps)
        if missing:
            print(f"\n[安装建议]")
            print(DependencyChecker.suggest_installation(vector_deps))


def check_and_warn():
    """检查依赖并给出警告。"""
    vector_deps = DependencyChecker.check_vector_dependencies()
    missing = DependencyChecker.get_missing_dependencies(vector_deps)

    if missing and "chromadb" in missing:
        import warnings
        warnings.warn(
            "向量数据库依赖未安装。RAG 功能将被禁用。\n"
            "安装命令：pip install 'gamedoctor[vector]'",
            ImportWarning,
            stacklevel=2
        )


if __name__ == "__main__":
    DependencyChecker.print_dependency_status()