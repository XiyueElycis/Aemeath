"""SearchEngine 单测 — 3 provider 降级与 DDG HTML 解析。

要点：
- DDG HTML 解析提取链接/标题/摘要
- Tavily/SerpAPI 无 key 抛 SearchError（跳过）
- SearchEngine 铨选：配 key 的 provider 优先，失败后降级
- 多查询每条独立降级
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from gamedoctor.config import Config
from gamedoctor.errors import SearchError
from gamedoctor.models import SearchResult, SourceType
from gamedoctor.searcher.adapters import DuckDuckGoProvider, SearchEngine, TavilyProvider


def test_duckduckgo_success():
    """DDG HTML 解析成功。"""
    # mock DDG HTML 片段
    html = """
    <html>
    <div class="results">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ftest">Example</a>
        <a class="result__snippet">This is a test snippet</a>
    </div>
    </html>
    """
    with patch("gamedoctor.searcher.adapters.httpx.get") as mock_get:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_get.return_value = mock_resp

        # 注意：provider 在构造时绑定 httpx.get，必须在 patch 生效后实例化
        provider = DuckDuckGoProvider()
        results = provider.search("test query", top_k=1)
        assert len(results) == 1
        assert results[0].title == "Example"
        assert results[0].url == "https://example.com/test"
        assert results[0].snippet == "This is a test snippet"
        assert results[0].source == "duckduckgo"


def test_duckduckgo_empty_result():
    """DDG 无结果抛 SearchError。"""
    html = "<html><div class='results'>No results</div></html>"
    with patch("gamedoctor.searcher.adapters.httpx.get") as mock_get:
        mock_resp = Mock()
        mock_resp.text = html
        mock_get.return_value = mock_resp

        provider = DuckDuckGoProvider()
        with pytest.raises(SearchError):
            provider.search("test", top_k=1)


def test_tavily_no_key():
    """Tavily 无 key 抛 SearchError。"""
    cfg = Config()
    provider = TavilyProvider(cfg)

    with pytest.raises(SearchError):
        provider.search("test", top_k=1)


@patch("gamedoctor.searcher.adapters.httpx.post")
def test_tavily_with_key(post_mock):
    """Tavily 有 key 成功检索。"""
    cfg = Config()
    cfg.set_secret("search/tavily/api_key", "fake-key")
    provider = TavilyProvider(cfg)

    mock_resp = Mock()
    mock_resp.__int__ = lambda: 200
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "title": "Test Title",
                "url": "https://example.com",
                "content": "Test content"
            }
        ]
    }
    post_mock.return_value = mock_resp

    results = provider.search("test", top_k=1)
    assert len(results) == 1
    assert results[0].title == "Test Title"
    assert results[0].url == "https://example.com"


def test_search_engine_fallback():
    """SearchEngine 铎选降级。"""
    cfg = Config()
    cfg.set_secret("search/tavily/api_key", "fake-key")  # 配 Tavily key
    engine = SearchEngine(cfg)

    # mock Tavily 抛错误、DDG 成功
    with patch("gamedoctor.searcher.adapters.TavilyProvider.search") as mock_tavily, \
         patch("gamedoctor.searcher.adapters.DuckDuckGoProvider.search") as mock_ddg:
        mock_tavily.side_effect = SearchError("Network error")
        mock_ddg.return_value = [
            SearchResult("DDG Result", "http://ddg.com", "DDG snippet", "duckduckgo", 0)
        ]

        results = engine.search(["test query"])
        assert len(results) == 1
        assert results[0].title == "DDG Result"
        # 验证调用顺序：先 Tavily，失败后调用 DDG
        mock_tavily.assert_called_once()
        mock_ddg.assert_called_once()


def test_multi_query_fallback():
    """多查询各自独立降级。"""
    cfg = Config()
    engine = SearchEngine(cfg)  # 无 key，只走 DDG

    with patch("gamedoctor.searcher.adapters.DuckDuckGoProvider.search") as mock_ddg:
        mock_ddg.return_value = [
            SearchResult("Result", "http://test.com", "snippet", "duckduckgo", 0)
        ]

        results = engine.search(["query1", "query2"])
        assert len(results) == 2  # 每个查询一条结果
        # 两个查询都被调用，top_k 以位置参数传入（默认 5）
        assert [c.args[0] for c in mock_ddg.call_args_list] == ["query1", "query2"]
        assert all(c.args[1] == 5 for c in mock_ddg.call_args_list)


def test_max_searches_limit():
    """限制最大搜索数。"""
    cfg = Config(data={"search": {"max_searches": 2, "provider": "duckduckgo"}})
    engine = SearchEngine(cfg)

    with patch("gamedoctor.searcher.adapters.DuckDuckGoProvider.search") as mock_ddg:
        mock_ddg.return_value = [
            SearchResult("Result", "http://test.com", "snippet", "duckduckgo", 0)
        ]

        results = engine.search(["q1", "q2", "q3"])
        assert len(results) == 2  # 只搜索前 2 个查询
        assert mock_ddg.call_count == 2  # 只调用 2 次
