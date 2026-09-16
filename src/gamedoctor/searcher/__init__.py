"""④ 多源联网检索（PRD §4.3 动态知识获取）。

- :mod:`templates` —— 按引擎/平台预制的查询模板
- :mod:`adapters` —— 数据源适配器（Tavily / SerpAPI / DuckDuckGo 三级降级，
  结构化数据源 PCGamingWiki / ProtonDB / WineHQ 优先）
"""

# 统一导出搜索适配器（三级降级）与查询模板（按引擎/平台匹配）
from .adapters import SearchProvider, SearchEngine, search
from .templates import QueryTemplate, build_queries

__all__ = ["SearchProvider", "SearchEngine", "search", "QueryTemplate", "build_queries"]
