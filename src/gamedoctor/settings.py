"""GUI 运行时设置存储（LLM 配置 + 智能体启用开关）。

与 :mod:`gamedoctor.config`（CLI 的 TOML 配置）解耦，单独存一个 JSON 文件，便于
桌面 GUI 的设置面板读写。默认路径 ``~/.gamedoctor/agent_settings.json``。

- LLM 配置：provider / model / api_base / api_key（当前生效组合）
- API Key 库：按 provider 各存一份（api_keys），切换厂商时自动带出对应密钥
- 上次模型：按 provider 记录上次使用的模型（provider_models），切回厂商时恢复
- 智能体启用：只记录「显式关闭」的智能体，未记录的默认启用
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .config import app_dir
from .llm.catalog import default_api_base, default_model

_SETTINGS_FILENAME = "agent_settings.json"


class Settings:
    """线程安全的运行时设置门面。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (app_dir() / _SETTINGS_FILENAME)
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # 存取通用
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        # 保证顶层结构存在
        self._data.setdefault("llm", {})
        self._data.setdefault("agents", {})
        self._data.setdefault("api_keys", {})
        self._data.setdefault("provider_models", {})
        self._data.setdefault("skills", {})
        self._data.setdefault("mcps", {})

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # LLM 配置
    # ------------------------------------------------------------------ #
    def get_llm(self) -> Dict[str, Any]:
        """返回当前 LLM 配置（provider/model/api_base/api_key）。

        api_key 为空时自动回落到该 provider 在密钥库中保存的密钥，
        实现「切换厂商自动带出对应 API Key」。
        """
        with self._lock:
            llm = dict(self._data.get("llm", {}))
            api_keys = dict(self._data.get("api_keys", {}))
        provider = llm.get("provider") or "deepseek"
        llm.setdefault("provider", provider)
        llm.setdefault("model", llm.get("model") or default_model(provider))
        llm.setdefault("api_base", llm.get("api_base") or default_api_base(provider))
        llm.setdefault("api_key", llm.get("api_key") or "")
        if not llm["api_key"]:
            llm["api_key"] = api_keys.get(provider, "")
        return llm

    def set_llm(self, provider: str, model: str, api_base: str, api_key: str) -> None:
        """更新 LLM 配置并落盘。api_key 传空串表示不覆盖已存密钥。

        显式传入密钥时，同时写入密钥库（api_keys[provider]），
        供以后切回该厂商时自动带出。
        """
        with self._lock:
            llm = self._data.setdefault("llm", {})
            llm["provider"] = provider
            llm["model"] = model or default_model(provider)
            llm["api_base"] = api_base or default_api_base(provider)
            # 记录该厂商上次使用的模型（切回厂商时恢复）
            self._data.setdefault("provider_models", {})[provider] = llm["model"]
            if api_key:  # 仅在用户显式填写时更新，避免误清空
                llm["api_key"] = api_key
                self._data.setdefault("api_keys", {})[provider] = api_key
            elif not llm.get("api_key"):
                # 切换厂商但未填密钥：回落到密钥库中该厂商的密钥
                llm["api_key"] = self._data.get("api_keys", {}).get(provider, "")
            self._save()

    # ------------------------------------------------------------------ #
    # 上次使用的模型（按 provider 记录）
    # ------------------------------------------------------------------ #
    def get_provider_model(self, provider: str) -> str:
        """取某 provider 上次使用的模型，未记录返回空串（调用方回落到默认模型）。"""
        with self._lock:
            return str(self._data.get("provider_models", {}).get(provider, ""))

    def provider_models(self) -> Dict[str, str]:
        """返回全部 {provider: 上次模型} 的副本。"""
        with self._lock:
            return dict(self._data.get("provider_models", {}))

    # ------------------------------------------------------------------ #
    # API Key 密钥库（按 provider 各存一份）
    # ------------------------------------------------------------------ #
    def get_api_key(self, provider: str) -> str:
        """取某 provider 已保存的 API Key（明文），未保存返回空串。"""
        with self._lock:
            return str(self._data.get("api_keys", {}).get(provider, ""))

    def set_api_key(self, provider: str, key: str) -> None:
        """保存某 provider 的 API Key。"""
        if not key:
            return
        with self._lock:
            self._data.setdefault("api_keys", {})[provider] = key
            # 若恰是当前厂商，同步当前生效密钥
            if self._data.get("llm", {}).get("provider") == provider:
                self._data["llm"]["api_key"] = key
            self._save()

    def delete_api_key(self, provider: str) -> None:
        """删除某 provider 已保存的 API Key。"""
        with self._lock:
            self._data.get("api_keys", {}).pop(provider, None)
            if self._data.get("llm", {}).get("provider") == provider:
                self._data["llm"]["api_key"] = ""
            self._save()

    def list_api_keys(self) -> Dict[str, str]:
        """返回全部已保存密钥的副本 {provider: key}。"""
        with self._lock:
            return dict(self._data.get("api_keys", {}))

    # ------------------------------------------------------------------ #
    # 智能体启用
    # ------------------------------------------------------------------ #
    def is_agent_enabled(self, name: str) -> bool:
        """智能体是否启用（默认启用）。"""
        with self._lock:
            return bool(self._data.get("agents", {}).get(name, True))

    def set_agent_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._data.setdefault("agents", {})[name] = bool(enabled)
            self._save()

    def enabled_map(self, names: list[str]) -> Dict[str, bool]:
        """按给定名单返回 enabled 映射。"""
        return {name: self.is_agent_enabled(name) for name in names}

    # ------------------------------------------------------------------ #
    # Skill 启用
    # ------------------------------------------------------------------ #
    def is_skill_enabled(self, name: str) -> bool:
        """技能是否启用（默认启用）。"""
        with self._lock:
            return bool(self._data.get("skills", {}).get(name, True))

    def set_skill_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._data.setdefault("skills", {})[name] = bool(enabled)
            self._save()

    # ------------------------------------------------------------------ #
    # MCP 启用
    # ------------------------------------------------------------------ #
    def is_mcp_enabled(self, name: str) -> bool:
        """MCP 是否启用（默认启用）。"""
        with self._lock:
            return bool(self._data.get("mcps", {}).get(name, True))

    def set_mcp_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._data.setdefault("mcps", {})[name] = bool(enabled)
            self._save()


# 进程级单例
_settings = Settings()


def get_settings() -> Settings:
    """获取全局设置单例。"""
    return _settings