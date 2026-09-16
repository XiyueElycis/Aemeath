"""数据源适配器（PRD §4.3 三级降级 + 结构化数据源优先）。

- 结构化数据源（PCGamingWiki / ProtonDB / WineHQ）优先于泛搜索（P1 钩子，P0 占位）。
- 泛搜索三级降级：Tavily（主）→ SerpAPI → DuckDuckGo（免 key 兜底）。

每个 Provider 实现 :meth:`SearchProvider.search` 与 :meth:`is_available`；
:class:`SearchEngine` 按"配置优先 + 规范降级链"逐 query 尝试，首个可用且返回
结果的 Provider 命中即停，无 key 的 Provider 自动跳过，链尾 DuckDuckGo 兜底。
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Callable, Protocol
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import Config, load
from ..errors import SearchError
from ..models import SearchResult

# 浏览器 UA：DDG 会拦截无 UA / 默认 httpx UA 的请求（返回 202/429）
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GameDoctor/0.1"
_HTTP_TIMEOUT = 20.0


class SearchProvider(Protocol):
    """搜索数据源协议。"""

    name: str

    def is_available(self) -> bool:
        """是否可用（如是否配置了 API Key）。免 key 源恒为 True。"""
        ...

    def search(self, query: str, top_k: int) -> list[SearchResult]:
        """执行一次检索，返回 Top K 结果。失败应抛 :class:`SearchError`。"""
        ...


# --------------------------------------------------------------------------- #
# DuckDuckGo（免 key，兜底）—— stdlib HTMLParser 解析，无新依赖
# --------------------------------------------------------------------------- #


def _clean_ddg_url(href: str) -> str:
    """把 DDG 包裹的外链还原为真实 URL。

    DDG 把结果链接包成 ``//duckduckgo.com/l/?uddg=<URL编码>&rut=...``；
    ``parse_qs`` 已对查询串做一次 URL 解码，故此处不再二次 ``unquote``。
    """
    if not href:
        return ""
    if "uddg=" in href:
        full = href if href.startswith("http") else "https:" + href
        vals = parse_qs(urlparse(full).query).get("uddg")
        if vals:
            return vals[0]
    return href if href.startswith("http") else ""


class _DDGHTMLParser(HTMLParser):
    """从 DDG html 结果页提取 (url, title, snippet)。

    DDG 结果块结构：``<a class="result__a" href="...">标题</a>`` 紧跟
    ``<a class="result__snippet">摘要</a>``。两者按出现顺序一一配对。
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []     # (clean_url, title)
        self.snippets: list[str] = []
        self._in_a = False
        self._cur_href = ""
        self._cur_title: list[str] = []
        self._in_snippet = False
        self._cur_snippet: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:  # type: ignore[override]
        if tag != "a":
            return
        cls = dict(attrs).get("class", "")
        if "result__a" in cls:
            self._in_a = True
            self._cur_href = dict(attrs).get("href", "")
            self._cur_title = []
        elif "result__snippet" in cls:
            self._in_snippet = True
            self._cur_snippet = []

    def handle_endtag(self, tag) -> None:  # type: ignore[override]
        if tag != "a":
            return
        if self._in_a:
            self._in_a = False
            clean = _clean_ddg_url(self._cur_href)
            title = " ".join(p.strip() for p in self._cur_title if p.strip())
            if clean:
                self.links.append((clean, title))
        if self._in_snippet:
            self._in_snippet = False
            text = " ".join(p.strip() for p in self._cur_snippet if p.strip())
            self.snippets.append(text)

    def handle_data(self, data) -> None:  # type: ignore[override]
        if self._in_a:
            self._cur_title.append(data)
        if self._in_snippet:
            self._cur_snippet.append(data)

    @property
    def results(self) -> list[tuple[str, str, str]]:
        """配对的 (url, title, snippet) 列表。"""
        out: list[tuple[str, str, str]] = []
        for i in range(min(len(self.links), len(self.snippets))):
            url, title = self.links[i]
            out.append((url, title, self.snippets[i]))
        return out


class DuckDuckGoProvider:
    """DuckDuckGo（免 key 兜底）—— html 端点 GET + 解析。"""

    name = "duckduckgo"

    def __init__(self, config: Config | None = None, *,
                 get_fn: Callable[..., httpx.Response] | None = None):
        self.config = config or load()
        self._get = get_fn or httpx.get

    def is_available(self) -> bool:
        return True

    def search(self, query: str, top_k: int) -> list[SearchResult]:
        url = "https://html.duckduckgo.com/html/"
        headers = {"User-Agent": _BROWSER_UA}
        try:
            resp = self._get(url, params={"q": query}, headers=headers, timeout=_HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SearchError(f"DuckDuckGo 网络错误: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise SearchError(f"DuckDuckGo HTTP {resp.status_code}")
        parser = _DDGHTMLParser()
        try:
            parser.feed(resp.text)
        except Exception as exc:  # noqa: BLE001 - 解析失败降级为该 query 无结果
            raise SearchError(f"DuckDuckGo 解析失败: {exc.__class__.__name__}") from exc
        out: list[SearchResult] = []
        for i, (u, title, snip) in enumerate(parser.results[:top_k]):
            out.append(SearchResult(title=title, url=u, snippet=snip,
                                    source="duckduckgo", rank=i))
        return out


# --------------------------------------------------------------------------- #
# Tavily（主，带 key）
# --------------------------------------------------------------------------- #


class TavilyProvider:
    """Tavily（主）—— POST ``api.tavily.com/search``。"""

    name = "tavily"

    def __init__(self, config: Config | None = None, *,
                 post_fn: Callable[..., httpx.Response] | None = None):
        self.config = config or load()
        self._key = self.config.secret("search/tavily/api_key") or ""
        self._post = post_fn or httpx.post

    def is_available(self) -> bool:
        return bool(self._key)

    def search(self, query: str, top_k: int) -> list[SearchResult]:
        if not self._key:
            raise SearchError("Tavily 未配置 api_key")
        try:
            resp = self._post(
                "https://api.tavily.com/search",
                json={"api_key": self._key, "query": query, "max_results": top_k},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"Tavily 网络错误: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise SearchError(f"Tavily HTTP {resp.status_code}")
        try:
            items = resp.json().get("results", [])
        except ValueError as exc:
            raise SearchError("Tavily 返回无法解析") from exc
        return [
            SearchResult(
                title=it.get("title", ""), url=it.get("url", ""),
                snippet=it.get("content", ""), source="tavily", rank=i,
            )
            for i, it in enumerate(items[:top_k])
        ]


# --------------------------------------------------------------------------- #
# SerpAPI（降级 2，带 key）
# --------------------------------------------------------------------------- #


class SerpApiProvider:
    """SerpAPI（降级 2）—— GET ``serpapi.com/search``。"""

    name = "serpapi"

    def __init__(self, config: Config | None = None, *,
                 get_fn: Callable[..., httpx.Response] | None = None):
        self.config = config or load()
        self._key = self.config.secret("search/serpapi/api_key") or ""
        self._get = get_fn or httpx.get

    def is_available(self) -> bool:
        return bool(self._key)

    def search(self, query: str, top_k: int) -> list[SearchResult]:
        if not self._key:
            raise SearchError("SerpAPI 未配置 api_key")
        try:
            resp = self._get(
                "https://serpapi.com/search",
                params={"engine": "google", "q": query, "api_key": self._key, "num": top_k},
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"SerpAPI 网络错误: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise SearchError(f"SerpAPI HTTP {resp.status_code}")
        try:
            items = resp.json().get("organic_results", [])
        except ValueError as exc:
            raise SearchError("SerpAPI 返回无法解析") from exc
        return [
            SearchResult(
                title=it.get("title", ""), url=it.get("link", ""),
                snippet=it.get("snippet", ""), source="serpapi", rank=i,
            )
            for i, it in enumerate(items[:top_k])
        ]


# name -> 类（供 SearchEngine 构建降级链；__init__ 需传 config）
PROVIDERS: dict[str, type[SearchProvider]] = {
    "tavily": TavilyProvider,
    "serpapi": SerpApiProvider,
    "duckduckgo": DuckDuckGoProvider,
}

# 规范降级顺序：配置 provider 优先，其余按此顺序补齐，DuckDuckGo 兜底
_CANONICAL_CHAIN = ("tavily", "serpapi", "duckduckgo")


class SearchEngine:
    """编排器：按"配置优先 + 规范降级链"逐 query 检索并限流。

    无 key 的 Tavily/SerpAPI 经 :meth:`is_available` 直接跳过；某 query 全链失败
    则贡献空结果。整体不会因单源失败而中断。
    """

    def __init__(self, config: Config | None = None):
        self.config = config or load()
        self._max_searches = int(self.config.get("search.max_searches", 5))
        self._top_k = int(self.config.get("search.top_k", 5))
        self._chain = self._build_chain()

    def _build_chain(self) -> list[SearchProvider]:
        """配置 provider 优先，其后接规范顺序里剩余的（DuckDuckGo 兜底）。"""
        configured = str(self.config.get("search.provider", "tavily"))
        order = [configured] + [p for p in _CANONICAL_CHAIN if p != configured]
        providers: list[SearchProvider] = []
        for name in order:
            cls = PROVIDERS.get(name)
            if cls is not None and all(p.name != name for p in providers):
                providers.append(cls(self.config))
        return providers

    def search(self, queries: list[str]) -> list[SearchResult]:
        available = [p for p in self._chain if p.is_available()]
        if not available:
            return []
        results: list[SearchResult] = []
        # 限流：最多执行 max_searches 次查询（PRD §4.3 每次诊断最多 5 次）
        for q in queries[: self._max_searches]:
            for provider in available:  # 逐 provider 降级，命中即停
                try:
                    got = provider.search(q, self._top_k)
                except SearchError:
                    continue
                if got:
                    results.extend(got)
                    break
        return results


def search(queries: list[str]) -> list[SearchResult]:
    """模块级快捷入口：用默认配置执行多查询检索。"""
    return SearchEngine().search(queries)
