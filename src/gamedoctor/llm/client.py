"""LLM 客户端（httpx 封装，PRD §4.6）。

对接 deepseek / Qwen / 智谱 GLM / Ollama 等 OpenAI 兼容端点。密钥走
``config.secret("llm/<provider>/api_key")``（系统 Keyring），回退到 provider 对应
环境变量；**绝不硬编码、绝不打印明文**。

支持两类调用：

- :meth:`HttpLLMClient.complete`    —— 单轮补全（一句话 prompt）
- :meth:`HttpLLMClient.complete_messages` —— 多轮消息（system/assistant/user），
  供「大模型自主决策调用智能体」的对话编排使用。

构造时可用 ``provider`` / ``api_base`` / ``api_key`` 参数直接覆盖配置，供桌面
GUI 运行时设置（settings.json）注入，无需改动 config.toml。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Protocol

import httpx

from ..config import Config, load
from ..errors import LLMError
from .catalog import default_api_base, default_model

# provider -> 默认模型名（配置里 model_* 留空时使用）
_PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "deepseek": "deepseek-chat",
}
# provider -> 密钥环境变量名（keyring 没有时回退）
_PROVIDER_ENV_KEY: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "doubao": "ARK_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}


class LLMClient(Protocol):
    """LLM 客户端协议。"""

    def complete(self, prompt: str, model: str | None = None) -> str:
        """同步完成一次补全，返回文本。"""
        ...


class HttpLLMClient:
    """基于 httpx 的默认实现（deepseek / Qwen / 任意 OpenAI 兼容端点）。

    :param post_fn: 测试注入用的可替换 HTTP POST 函数；默认 ``httpx.post``。
    :param provider / api_base / api_key: 运行时覆盖配置（供 GUI 设置注入）。
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        post_fn: Callable[..., httpx.Response] | None = None,
        provider: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ):
        self.config = config or load()
        self._provider = provider or str(self.config.get("llm.provider", "deepseek"))
        self._api_base = (
            api_base
            or str(self.config.get("llm.api_base", ""))
            or default_api_base(self._provider)
        )
        self._api_key_override = api_key
        self._timeout = float(self.config.get("llm.timeout", 60))
        # 注入点：测试可不 monkeypatch httpx 即可控制响应
        self._post = post_fn or httpx.post

    # ------------------------------------------------------------------ #
    def complete(self, prompt: str, model: str | None = None,
                 temperature: float | None = None, max_tokens: int | None = None) -> str:
        """单轮补全（等价于一条 user 消息）。"""
        return self.complete_messages(
            [{"role": "user", "content": prompt}], model=model,
            temperature=temperature, max_tokens=max_tokens,
        )

    def complete_messages(self, messages: list[dict[str, Any]], model: str | None = None,
                          temperature: float | None = None,
                          max_tokens: int | None = None) -> str:
        """多轮消息补全，返回文本。

        密钥缺失、网络异常、非 2xx、响应结构异常均抛 :class:`LLMError`，
        由上层降级处理，不中断整体流程。

        :param temperature: 采样温度；None 时用默认 0.2（求稳，降低 JSON 漂移）。
        :param max_tokens: 最大生成长度；None 时不传（走 provider 默认）。
        """
        api_key = self._resolve_key()
        model = model or self._default_model()
        url = f"{self._api_base}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else 0.2,
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        try:
            resp = self._post(url, json=body, headers=headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise LLMError(f"LLM 请求超时 ({self._timeout}s)") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 网络错误: {exc.__class__.__name__}") from exc

        if resp.status_code >= 400:
            # 不回显响应体：部分 provider 错误体会回显请求内容，可能含敏感信息
            raise LLMError(f"LLM 返回 HTTP {resp.status_code}")

        try:
            data = resp.json()
            return str(data["choices"][0]["message"]["content"]).strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM 返回无法解析") from exc

    # ------------------------------------------------------------------ #
    def _resolve_key(self) -> str:
        """密钥解析：显式注入 → keyring → provider 对应环境变量 → 缺失抛错。

        按 PRD §7 风险对策"API Key 泄露 → 系统 Keyring 存储，不明文打印"，
        全程不打印密钥，错误提示只引导用户去配置，不暴露任何凭据片段。
        """
        if self._api_key_override:
            return self._api_key_override
        key = self.config.secret(f"llm/{self._provider}/api_key") or ""
        if not key:
            env_name = _PROVIDER_ENV_KEY.get(self._provider)
            if env_name:
                key = os.environ.get(env_name, "")
        if not key:
            env_hint = _PROVIDER_ENV_KEY.get(self._provider, "对应环境变量")
            raise LLMError(
                f"未配置 LLM 密钥：请运行 "
                f"`gamedoctor config set-secret llm/{self._provider}/api_key <key>`，"
                f"或设置环境变量 {env_hint}"
            )
        return key

    def _default_model(self) -> str:
        """provider 默认模型；未知 provider 不静默猜测，直接报错。"""
        model = _PROVIDER_DEFAULT_MODEL.get(self._provider) or default_model(self._provider)
        if not model:
            raise LLMError(f"未知 LLM provider: {self._provider}（未配置默认模型）")
        return model