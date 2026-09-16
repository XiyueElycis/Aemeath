"""零配置知识灌库：把 ``rag/<game>/*.md`` 排障文档写入 SQLite entries 表。

文档 front-matter 没有 signature 字段，错误签名散落在正文 ``# 常见错误日志``
节的反引号片段里（如 ``java.lang.UnsatisfiedLinkError``），因此一篇文档会
提取多条签名，全部指向同一份修复模板（``# 解决方案`` 节）。

幂等性：store 建表时已对 signature 建唯一索引，灌库走 INSERT OR REPLACE，
重复执行不会产生重复行。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .store import SQLiteKnowledgeStore

logger = logging.getLogger(__name__)

# seed.py 位于 src/gamedoctor/knowledge/，上溯 3 级到项目根，rag/ 与其平级
DEFAULT_RAG_ROOT = Path(__file__).resolve().parents[3] / "rag"

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_SIGNATURE_MAX_LEN = 200


def parse_document(text: str) -> Optional[Dict[str, Any]]:
    """解析一篇排障 markdown，返回结构化 dict；非排障文档返回 None。

    - front-matter：title/game/category/tags（tags 是 JSON 风格数组）；
    - sections：``# 标题`` → 正文；
    - signatures：常见错误日志节的反引号片段 + ASCII tags + 文件名变体。
    没有「解决方案」节的文件（空文件/索引文件）视为非排障文档跳过。
    """
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return None
    meta_text, body = match.group(1), match.group(2)
    meta = _parse_front_matter(meta_text)
    sections = _parse_sections(body)

    if "解决方案" not in sections:
        return None

    signatures = _extract_signatures(
        stem="",  # 由调用方按文件名补入
        tags=meta.get("tags", []),
        error_log=sections.get("常见错误日志", ""),
        include_stem=False,
    )
    if not signatures:
        return None

    template = str(meta.get("title") or "").strip()
    solution = sections["解决方案"].strip()
    if solution:
        template = f"{template}\n\n{solution}" if template else solution

    return {
        "title": meta.get("title", ""),
        "game": meta.get("game", ""),
        "category": meta.get("category", ""),
        "tags": meta.get("tags", []),
        "signatures": signatures,
        "repair_template": template,
    }


def _parse_front_matter(meta_text: str) -> Dict[str, Any]:
    """解析我们自己文档里出现的简单 YAML 键值（不引第三方 YAML 依赖）。"""
    meta: Dict[str, Any] = {}
    for line in meta_text.splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("["):
            try:
                meta[key] = [str(t).strip() for t in json.loads(raw.replace("'", '"'))]
            except json.JSONDecodeError:
                meta[key] = []
        else:
            meta[key] = "" if raw in ("NULL", "null", '""', "''") else raw.strip('"').strip("'")
    return meta


def _parse_sections(body: str) -> Dict[str, str]:
    """按一级标题 ``# xxx`` 切节，返回 {标题: 正文}（不含更下级标题层级）。"""
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in body.splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def _extract_signatures(stem: str, tags: List[str], error_log: str,
                        include_stem: bool = True) -> List[str]:
    """从错误日志节反引号片段、ASCII tags、文件名生成去重签名列表。"""
    seen: set[str] = set()
    out: List[str] = []

    def _add(value: str) -> None:
        v = value.strip()
        if not v or len(v) > _SIGNATURE_MAX_LEN or v in seen:
            return
        seen.add(v)
        out.append(v)

    # 1) 错误日志节里的反引号片段（最贴近真实报错，质量最高）
    for frag in _BACKTICK_RE.findall(error_log or ""):
        _add(frag)

    # 2) ASCII tags（中文 tag 对英文错误串的 LIKE 匹配无意义，跳过）
    for tag in tags or []:
        if tag.isascii() and len(tag) >= 3:
            _add(tag)

    # 3) 文件名变体：asi_loader_missing / "asi loader missing"
    if include_stem and stem:
        _add(stem)
        spaced = stem.replace("_", " ")
        if spaced != stem:
            _add(spaced)

    return out


def seed_from_rag(
    rag_root: Path | str | None = None,
    store: SQLiteKnowledgeStore | None = None,
) -> Dict[str, int]:
    """扫描 ``rag_root/<game>/*.md`` 灌入知识库。

    :return: ``{游戏目录名: 文档数}``；rag 目录不存在时返回空 dict。
    """
    root = Path(rag_root) if rag_root else DEFAULT_RAG_ROOT
    if not root.is_dir():
        logger.info("RAG 目录不存在，跳过灌库：%s", root)
        return {}

    own_store = store is None
    if own_store:
        store = SQLiteKnowledgeStore()
    store.connect()
    try:
        per_game: Dict[str, int] = {}
        for game_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            doc_count = 0
            for md_path in sorted(game_dir.glob("*.md")):
                try:
                    text = md_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    logger.warning("知识文档读取失败：%s", md_path, exc_info=True)
                    continue
                parsed = parse_document(text)
                if parsed is None:
                    continue
                # parse_document 的签名来自错误日志节与 tags；这里补文件名变体
                signatures = list(parsed["signatures"])
                for extra in (md_path.stem, md_path.stem.replace("_", " ")):
                    if extra and extra not in signatures and len(extra) <= _SIGNATURE_MAX_LEN:
                        signatures.append(extra)
                for sig in signatures:
                    store.upsert(sig, game_dir.name, parsed["repair_template"])
                doc_count += 1
            if doc_count:
                per_game[game_dir.name] = doc_count
        if per_game:
            logger.info("知识灌库完成：%s", per_game)
        return per_game
    finally:
        if own_store:
            store.close()
