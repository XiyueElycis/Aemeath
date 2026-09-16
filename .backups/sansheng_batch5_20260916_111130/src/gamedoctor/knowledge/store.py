"""本地知识库存储（SQLite）。

表结构（可扩展）：
- ``entries`` —— 知识条目：``(id, signature, tech_stack, repair_template, hit_count, reviewed)``
- ``diagnoses`` —— 诊断记录：``(diagnosis_id, game_name, created_at, result)``

当前为骨架：提供建表 + 增删查接口，具体检索策略见 :mod:`retriever`。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from ..config import Config, app_dir


class KnowledgeStore(Protocol):
    """知识存储协议。"""

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def upsert(self, signature: str, tech_stack: str, repair_template: str) -> None: ...
    def lookup(self, signature: str) -> list[dict]: ...


class SQLiteKnowledgeStore:
    """SQLite 实现骨架。"""

    def __init__(self, db_path: Path | None = None):
        # 未显式指定路径时，落在应用数据目录下的 knowledge.db
        self.db_path = db_path or (app_dir() / "knowledge.db")
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """打开数据库并幂等建表（表已存在则跳过）。"""
        # 确保数据库所在目录存在，再建立连接
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        # 知识条目表：签名 / 技术栈 / 修复动作序列 / 命中次数 / 是否人工复核
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature TEXT NOT NULL,
                tech_stack TEXT NOT NULL,
                repair_template TEXT NOT NULL,
                hit_count INTEGER DEFAULT 0,
                reviewed INTEGER DEFAULT 0
            )
            """
        )
        # 诊断记录表：一次诊断的结果落库，便于后续审计 / 统计
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diagnoses (
                diagnosis_id TEXT PRIMARY KEY,
                game_name TEXT,
                created_at TEXT,
                result TEXT
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        """关闭连接并清空句柄（幂等，可重复调用）。"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def upsert(self, signature: str, tech_stack: str, repair_template: str) -> None:
        """写入/更新一条知识三元组（错误签名, 技术栈, 修复动作序列）。"""
        assert self._conn is not None, "先调用 connect()"
        # 参数化 SQL 防注入；INSERT OR REPLACE 保证同签名幂等
        self._conn.execute(
            "INSERT OR REPLACE INTO entries (signature, tech_stack, repair_template) "
            "VALUES (?, ?, ?)",
            (signature, tech_stack, repair_template),
        )
        self._conn.commit()

    def lookup(self, signature: str) -> list[dict]:
        """按签名做模糊匹配，命中次数高的排在前面（Top 5）。"""
        assert self._conn is not None, "先调用 connect()"
        # LIKE 通配做"签名片段包含"匹配，后续可升级为向量检索（config.knowledge.use_vector）
        rows = self._conn.execute(
            "SELECT signature, tech_stack, repair_template, hit_count FROM entries "
            "WHERE signature LIKE ? ORDER BY hit_count DESC LIMIT 5",
            (f"%{signature}%",),
        ).fetchall()
        return [
            {"signature": r[0], "tech_stack": r[1], "repair_template": r[2], "hit_count": r[3]}
            for r in rows
        ]
