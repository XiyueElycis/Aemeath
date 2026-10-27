"""HttpLLMClient 单测 — 确保与 DeepSeek/Qwen/OpenAI 兼容性。

要点：
- 无密钥时抛 LLMError
- 非 2xx/choices 异常抛 LLMError
- 成功时返回 content
- 接收 provider/model 配置与默认 fallback
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from httpx import HTTPStatusError

from aemeath_agent.config import Config
from aemeath_agent.errors import LLMError
from aemeath_agent.llm.client import HttpLLMClient


def _client_with_key() -> HttpLLMClient:
    """构造带假密钥的客户端（不触碰真实 keyring / 环境变量）。"""
    return HttpLLMClient(Config(), api_key="fake-key")


def test_no_key(monkeypatch):
    """未配置密钥时抛 LLMError。

    keyring 与环境变量两层都要屏蔽：``_resolve_key`` 在 keyring 取不到时会回退到
    provider 对应的环境变量，本机只要设了 ``DEEPSEEK_API_KEY`` 之类的变量，用例就
    会「取到密钥」而失效（表现为 DID NOT RAISE）。
    """
    monkeypatch.setattr(Config, "secret", lambda self, name: None)
    for env_name in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY",
                     "MOONSHOT_API_KEY", "ARK_API_KEY", "MINIMAX_API_KEY",
                     "SILICONFLOW_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)

    cli = HttpLLMClient(Config())
    with pytest.raises(LLMError, match="未配置 LLM 密钥"):
        cli.complete("test")


@patch("aemeath_agent.llm.client.httpx.post")
def test_success_with_content(post_fn_mock):
    """成功调用并返回 content。"""
    cli = _client_with_key()

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "fake response"}}]
    }
    post_fn_mock.return_value = mock_resp

    res = cli.complete("prompt")
    assert res == "fake response"


@patch("aemeath_agent.llm.client.httpx.post")
def test_http_error(post_fn_mock):
    """HTTP 网络层异常抛 LLMError。"""
    cli = _client_with_key()

    post_fn_mock.side_effect = HTTPStatusError("500", request=Mock(), response=Mock())

    with pytest.raises(LLMError, match="网络错误"):
        cli.complete("prompt")


@patch("aemeath_agent.llm.client.httpx.post")
def test_http_401(post_fn_mock):
    """非 2xx 响应抛 LLMError 并带状态码。"""
    cli = _client_with_key()

    mock_resp = Mock()
    mock_resp.status_code = 401
    post_fn_mock.return_value = mock_resp

    with pytest.raises(LLMError, match="HTTP 401"):
        cli.complete("prompt")


@patch("aemeath_agent.llm.client.httpx.post")
def test_no_choices(post_fn_mock):
    """无 choices 字段抛 LLMError。"""
    cli = _client_with_key()

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"error": "bad request"}
    post_fn_mock.return_value = mock_resp

    with pytest.raises(LLMError, match="无法解析"):
        cli.complete("prompt")


@patch("aemeath_agent.llm.client.httpx.post")
def test_default_model(post_fn_mock):
    """不传 model 时使用 provider 默认值。"""
    cli = _client_with_key()

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "fake"}}]
    }
    post_fn_mock.return_value = mock_resp

    cli.complete("prompt")  # 无 model 参数
    args, kwargs = post_fn_mock.call_args
    assert "deepseek-chat" in kwargs["json"]["model"]


@patch("aemeath_agent.llm.client.httpx.post")
def test_custom_model(post_fn_mock):
    """显式传 model 时使用指定值。"""
    cli = _client_with_key()

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "fake"}}]
    }
    post_fn_mock.return_value = mock_resp

    cli.complete("prompt", model="gpt-4")
    args, kwargs = post_fn_mock.call_args
    assert kwargs["json"]["model"] == "gpt-4"
