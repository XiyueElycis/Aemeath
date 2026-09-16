"""批5：rag markdown 灌库 / 检索器自动 seed / 向量模块降级。"""

from __future__ import annotations

import pytest

from gamedoctor.knowledge import retriever as retriever_mod
from gamedoctor.knowledge import seed as seed_mod
from gamedoctor.knowledge.retriever import SQLiteRetriever, _load_rag_data_module
from gamedoctor.knowledge.seed import DEFAULT_RAG_ROOT, parse_document, seed_from_rag
from gamedoctor.knowledge.store import SQLiteKnowledgeStore

LWJGL_DOC = """---
title: "LWJGL 原生库加载失败"
game: "Minecraft"
mod_name: NULL
category: "文件完整性问题"
tags: ["lwjgl", "natives", "dll", "闪退"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

启动即崩溃。

# 解决方案

1.恢复杀毒软件隔离区文件
2.重新下载 natives

# 常见错误日志

- `java.lang.UnsatisfiedLinkError: no lwjgl in java.library.path`
- `Failed to load native library`
"""

DX_DOC = """---
title: "缺少 DirectX 运行库"
game: "Minecraft"
mod_name: NULL
category: "环境缺失"
tags: ["directx", "dll"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

启动报 d3dx9 相关 dll 缺失。

# 解决方案

1.安装 DirectX 9.0c 运行库

# 常见错误日志

- `d3dx9_43.dll is missing from your computer`
"""

NO_SOLUTION_DOC = """---
title: "索引页"
game: "Minecraft"
tags: ["index"]
---

# 常见错误日志

- `should-not-be-indexed`
"""


@pytest.fixture()
def tmp_rag(tmp_path):
    mc = tmp_path / "rag" / "minecraft"
    mc.mkdir(parents=True)
    (mc / "lwjgl_native_load_failed.md").write_text(LWJGL_DOC, encoding="utf-8")
    (mc / "dx_missing.md").write_text(DX_DOC, encoding="utf-8")
    (mc / "minecraft.md").write_text("", encoding="utf-8")  # 空索引文件
    (mc / "index.md").write_text(NO_SOLUTION_DOC, encoding="utf-8")  # 无解决方案节
    gta = tmp_path / "rag" / "Grand_Theft_Auto_V"
    gta.mkdir(parents=True)
    (gta / "asi_loader_missing.md").write_text(
        DX_DOC.replace("DirectX 运行库", "ASI Loader"), encoding="utf-8"
    )
    return tmp_path / "rag"


@pytest.fixture()
def db_store(tmp_path):
    store = SQLiteKnowledgeStore(str(tmp_path / "knowledge.db"))
    store.connect()
    yield store
    store.close()


def test_parse_document_extracts_backtick_signatures():
    doc = parse_document(LWJGL_DOC)
    assert doc is not None
    assert "java.lang.UnsatisfiedLinkError: no lwjgl in java.library.path" in doc["signatures"]
    assert "Failed to load native library" in doc["signatures"]
    assert "lwjgl" in doc["tags"]
    assert "恢复杀毒软件隔离区文件" in doc["repair_template"]


def test_parse_document_skips_non_troubleshooting_docs():
    assert parse_document("") is None
    assert parse_document(NO_SOLUTION_DOC) is None


def test_seed_per_game_counts_and_lookup(tmp_rag, db_store):
    counts = seed_from_rag(tmp_rag, db_store)
    assert counts == {"Grand_Theft_Auto_V": 1, "minecraft": 2}

    hits = db_store.lookup("UnsatisfiedLinkError")
    assert hits and all(h["tech_stack"] == "minecraft" for h in hits)

    dx_hits = db_store.lookup("d3dx9")
    assert dx_hits, "d3dx9 错误签名应能模糊命中"
    assert "DirectX 9.0c" in dx_hits[0]["repair_template"]

    # 文件名变体签名也应入库
    assert db_store.lookup("lwjgl native load failed")


def test_seed_is_idempotent(tmp_rag, db_store):
    seed_from_rag(tmp_rag, db_store)
    first = db_store.count_entries()
    assert first > 0
    seed_from_rag(tmp_rag, db_store)
    assert db_store.count_entries() == first


def test_seed_missing_rag_dir_returns_empty(tmp_path, db_store):
    assert seed_from_rag(tmp_path / "no_such_rag", db_store) == {}
    assert db_store.count_entries() == 0


def test_sqlite_retriever_auto_seeds_empty_db(tmp_rag, tmp_path, monkeypatch):
    monkeypatch.setattr(seed_mod, "DEFAULT_RAG_ROOT", tmp_rag)
    store = SQLiteKnowledgeStore(str(tmp_path / "auto.db"))
    retriever = SQLiteRetriever(store)

    hits = retriever.search("d3dx9")
    assert hits, "首次检索空库应自动灌库并命中"

    # 第二次实例化对同一个库不再翻倍（幂等由唯一索引保证）
    store.connect()
    count_after = store.count_entries()
    store.close()
    store2 = SQLiteKnowledgeStore(str(tmp_path / "auto.db"))
    store2.connect()
    assert store2.count_entries() == count_after
    store2.close()


def test_retriever_seed_failure_does_not_block(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(str(tmp_path / "fail.db"))
    retriever = SQLiteRetriever(store)

    def _boom(*a, **k):
        raise RuntimeError("seed exploded")

    monkeypatch.setattr(seed_mod, "seed_from_rag", _boom)
    # 灌库异常被吞掉并降级为空结果，不应抛出
    assert retriever.search("anything") == []


def test_real_rag_corpus_smoke(tmp_path):
    """对仓库自带 rag/ 语料灌库（验收命令的真实形态）。"""
    if not DEFAULT_RAG_ROOT.is_dir():
        pytest.skip("仓库 rag/ 目录不存在")
    store = SQLiteKnowledgeStore(str(tmp_path / "real.db"))
    store.connect()
    try:
        counts = seed_from_rag(DEFAULT_RAG_ROOT, store)
        assert counts.get("minecraft", 0) >= 10
        assert counts.get("Grand_Theft_Auto_V", 0) >= 10
        assert store.lookup("UnsatisfiedLinkError"), "验收：Java 链接错误可命中"
        assert store.lookup("unsupported game version"), "验收：GTA ScriptHookV 签名可命中"
    finally:
        store.close()


def test_load_rag_data_module_never_raises():
    """无 vector extra 时 importlib 加载顶层 rag_data.py 必须优雅降级为 None。"""
    retriever_mod._rag_data_cache = None  # 重置缓存强制重新尝试
    # 无论环境是否安装 langchain，都应返回模块或 None，绝不抛异常
    result = _load_rag_data_module()
    assert result is None or hasattr(result, "RAGKnowledgeBase")
