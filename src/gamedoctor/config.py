"""配置管理（PRD §2 配置管理）。

- TOML 配置文件位于 ``~/.gamedoctor/config.toml``（首次运行时自动生成默认值）。
- 密钥（LLM API Key / 搜索 API Key）走系统 Keyring，**不明文落盘、不明文打印**。
- 读取用标准库 ``tomllib``（3.11+），3.10 回退到 ``tomli``。

仅暴露 :class:`Config` 一个门面对象，其余模块通过 ``config.load()`` 获取。
"""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 回退
    import tomli as tomllib

from .errors import ConfigError
from .utils.dependency_check import DependencyChecker

APP_DIR_NAME = ".gamedoctor"

# Keyring 服务名；密钥按 key 存，如 "llm/deepseek/api_key"、"search/tavily/api_key"
KEYRING_SERVICE = "gamedoctor"

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "deepseek",           # deepseek / qwen / ollama
        "api_base": "https://api.deepseek.com",
        "model_strong": "",               # 强模型（方案生成）
        "model_light": "",                # 轻量模型（分类 / 摘要）
        "model_critic": "",               # 交叉自检模型（留空则与生成模型不同）
    },
    "search": {
        "provider": "tavily",             # tavily / serpapi / duckduckgo
        "max_searches": 5,                # 每次诊断最多搜索次数（限流）
        "top_k": 5,                       # 每次取 Top K
        "structured_first": True,         # 结构化数据源优先（PCGamingWiki/WineHQ/ProtonDB）
    },
    "knowledge": {
        "db_path": "",                    # SQLite 路径，空则默认 ~/.gamedoctor/knowledge.db
        "use_vector": False,              # 是否启用向量检索（chromadb）
        "rag_data_dir": "",               # RAG 数据目录，空则默认项目 rag/ 目录
        "auto_index": True,               # 是否自动索引未索引的游戏
        "embedding_model": "BAAI/bge-small-zh-v1.5",  # 嵌入模型
    },
    "fixer": {
        "backup_dir": "",                 # 备份目录，空则默认 ~/.gamedoctor/backups
        "confirm_l2": True,               # L2 动作是否逐项确认
    },
}


def _home_dir() -> Path:
    return Path.home()


def app_dir() -> Path:
    """返回 ``~/.gamedoctor`` 并确保存在。"""
    p = _home_dir() / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_config_path() -> Path:
    return app_dir() / "config.toml"


@dataclass
class Config:
    """运行时配置门面。持有合并后的字典，并提供常见访问器。"""

    data: dict[str, Any] = field(default_factory=dict)
    config_path: Path = field(default_factory=default_config_path)

    # -- 访问器 ------------------------------------------------------------ #
    def get(self, dotted: str, default: Any = None) -> Any:
        """按 ``section.subsection.key`` 点分路径取配置，缺省返回 ``default``。

        例：``cfg.get("search.max_searches")`` → 先取 ``search`` 段，再取
        ``max_searches`` 键；任一层缺失即回退默认值。
        """
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def secret(self, key: str) -> str | None:
        """从系统 Keyring 取密钥，绝不打印明文。

        密钥独立于 config.toml，避免误提交到版本库；Keyring 后端不可用时
        返回 ``None``（调用方据此提示用户先 ``config set-secret``）。
        """
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, key)
        except Exception:  # pragma: no cover - keyring 后端不可用
            return None

    def set_secret(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(KEYRING_SERVICE, key, value)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并两个配置字典，``override`` 优先。

    递归的语义：两个都是 dict 的键继续向下合并，否则整体覆盖。这样用户只需
    在 config.toml 里写想改的键，未写的键保留默认值（不丢默认子项）。
    """
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Path | None = None) -> Config:
    """加载配置：不存在则生成默认文件；用户文件覆盖默认值。"""
    cfg_path = path or default_config_path()
    data: dict[str, Any] = _deep_merge(DEFAULT_CONFIG, {})

    if cfg_path.exists():
        try:
            with cfg_path.open("rb") as f:
                user = tomllib.load(f)
            data = _deep_merge(data, user)
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise ConfigError(f"配置文件解析失败: {cfg_path}: {exc}") from exc
    else:
        _write_default(cfg_path)

    # 检查并警告依赖问题
    _check_dependencies(data)

    return Config(data=data, config_path=cfg_path)


def _check_dependencies(data: dict[str, Any]):
    """检查依赖并给出警告。"""
    # 如果启用了向量检索，检查依赖
    if data.get("knowledge", {}).get("use_vector", False):
        if not DependencyChecker.check_vector_availability():
            warnings.warn(
                "向量检索已启用但缺少依赖。RAG 功能将被禁用。\n"
                "安装命令：pip install 'gamedoctor[vector]'",
                ImportWarning,
                stacklevel=3
            )


def _write_default(path: Path) -> None:
    """首次运行时写入带注释的默认配置模板。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # tomli_w 不支持注释，因此改用预置的字符串模板直接写盘
    template = _default_template()
    path.write_text(template, encoding="utf-8")


def _default_template() -> str:
    """返回带中文注释的默认配置模板（手写字符串，因 tomli_w 不支持注释）。"""
    return """\
# Game Doctor 配置。密钥请勿写在这里，用 `gamedoctor config set-secret <key> <value>`
# 存入系统 Keyring。可用密钥 key：llm/<provider>/api_key、search/<provider>/api_key

[llm]
provider = "deepseek"                # deepseek / qwen / ollama
api_base = "https://api.deepseek.com"
model_strong = ""                    # 方案生成强模型
model_light = ""                     # 分类/摘要轻量模型
model_critic = ""                    # 交叉自检模型（留空则与生成不同）

[search]
provider = "tavily"                  # tavily / serpapi / duckduckgo
max_searches = 5
top_k = 5
structured_first = true

[knowledge]
db_path = ""                        # SQLite 路径，空则默认 ~/.gamedoctor/knowledge.db
use_vector = false                 # 是否启用向量检索（chromadb）
rag_data_dir = ""                  # RAG 数据目录，空则默认项目 rag/ 目录
auto_index = true                  # 是否自动索引未索引的游戏
embedding_model = "BAAI/bge-small-zh-v1.5"  # 嵌入模型

[fixer]
backup_dir = ""
confirm_l2 = true
"""
