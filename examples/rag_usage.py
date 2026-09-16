"""RAG 功能使用示例。

展示如何使用 RAG 知识库进行游戏问题诊断。
"""

from __future__ import annotations

from gamedoctor.config import load
from gamedoctor.knowledge.retriever import search_knowledge, check_and_index_games
from gamedoctor.models import KnowledgeHit


def demo_rag_search():
    """演示 RAG 搜索功能。"""
    print("=== RAG 知识库搜索示例 ===\n")

    # 1. 检查并索引游戏
    print("1. 正在检查并索引游戏...")
    indexed_games = check_and_index_games()
    if indexed_games:
        print(f"   已索引的游戏: {indexed_games}")
    else:
        print("   没有找到可索引的游戏")

    print("\n2. 测试搜索...")

    # 2. 测试不同的搜索
    test_queries = [
        "0xc000007b",
        "启动报错 DLL 缺失",
        "GTA5 ASI loader",
        "闪退 黑屏"
    ]

    for query in test_queries:
        print(f"\n搜索: {query}")
        try:
            # 使用混合检索器
            cfg = load()
            if cfg.get("knowledge.use_vector", False):
                from gamedoctor.knowledge.retriever import HybridRetriever
                retriever = HybridRetriever(cfg)
                results = retriever.search(query, query)
            else:
                # 回退到 SQLite 检索
                results = search_knowledge(query)

            print(f"   找到 {len(results)} 个结果:")
            for i, hit in enumerate(results[:3], 1):
                print(f"   {i}. [{hit.score:.2f}] {hit.repair_template[:100]}...")
        except Exception as e:
            print(f"   搜索失败: {e}")


def demo_auto_index():
    """演示自动索引功能。"""
    print("\n=== 自动索引功能演示 ===\n")

    # 检查可用游戏
    try:
        from gamedoctor.knowledge.retriever import get_available_games
        games = get_available_games()
        print(f"数据目录中的游戏: {games}")
    except Exception as e:
        print(f"获取游戏列表失败: {e}")


if __name__ == "__main__":
    demo_rag_search()
    demo_auto_index()