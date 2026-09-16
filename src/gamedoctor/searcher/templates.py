"""搜索模板引擎（PRD §4.3 多源搜索模板化）。

按引擎 / 平台预制 Query 模板：UE → reddit/steamcommunity，Proton → protondb，
Unity → github issues 等。模板可增量维护。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import ErrorCategory, TechStackFingerprint


@dataclass(frozen=True)
class QueryTemplate:
    """一条搜索模板。``query`` 中的 ``{game}`` ``{signature}`` 会被替换。"""

    engine: str | None            # 匹配引擎，None 表示通用
    category: ErrorCategory | None
    source_hint: str              # 建议检索站点
    query: str


QUERY_TEMPLATES: tuple[QueryTemplate, ...] = (
    QueryTemplate("unreal", ErrorCategory.RUNTIME_CRASH, "reddit/steamcommunity",
                  "{game} crash {signature} fix site:reddit.com OR site:steamcommunity.com"),
    QueryTemplate("unity", None, "github issues",
                  "{game} {signature} site:github.com"),
    QueryTemplate(None, ErrorCategory.COMPATIBILITY, "protondb",
                  "{game} proton wine site:protondb.com"),
    QueryTemplate(None, ErrorCategory.LAUNCH_FAILURE, "pcgamingwiki",
                  "{game} {signature} site:pcgamingwiki.com"),
)


def build_queries(fp: TechStackFingerprint, category: ErrorCategory | None,
                  signature: str) -> list[QueryTemplate]:
    """根据技术栈指纹 + 错误类别挑选并填充模板。

    筛选规则：``engine`` 指定则必须匹配，``category`` 指定则必须匹配，二者为
    ``None`` 的模板视为"通用"始终命中。选中的模板用游戏名与错误签名填充占位符。
    """
    engine = fp.engine.engine if fp.engine else None
    out: list[QueryTemplate] = []
    for t in QUERY_TEMPLATES:
        # 模板指定引擎时须匹配指纹引擎，否则跳过
        if t.engine is not None and t.engine != engine:
            continue
        # 模板指定错误类别时须匹配当前类别，否则跳过
        if t.category is not None and t.category != category:
            continue
        # 填充 {game} 与 {signature} 占位符（签名可能为空）
        filled = QueryTemplate(
            engine=t.engine, category=t.category, source_hint=t.source_hint,
            query=t.query.format(game=fp.game_name, signature=signature or "").strip(),
        )
        out.append(filled)
    return out
