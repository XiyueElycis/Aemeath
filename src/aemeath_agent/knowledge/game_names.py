"""游戏名称匹配知识库：任意写法 → 规范游戏身份。

同一款游戏在本系统里会有多套名字——Steam 清单里的官方英文名、中文译名、
目录名、玩家俗称、错拼。之前各处的名字互不相认（目录叫「鸣潮」时匹配不到
清单里的 ``Wuthering Waves``），本模块给出唯一解析口径：

- :func:`normalize_name`：小写 + 折叠全角 + 去标点/空格/商标符号 +
  剥离「豪华版」/``Deluxe Edition`` 这类版本后缀；
- :func:`resolve_game`：三级匹配——归一化精确 → 双向包含（限长≥4，避免
  ``mc``/``ww`` 这类短别名误报）→ 近似匹配（容忍错拼）；
- :func:`display_name`：收录则给规范显示名（中文优先），未收录原样返回。

数据来源为包内 ``data/game_aliases.json``（主库，随版本走）+ 用户文件
``~/.aemeath_agent/game_aliases.json``（同 ``game_id`` 整体覆盖主库条目，
加游戏无需改代码）。
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Optional, Tuple

from ..config import app_dir

logger = logging.getLogger(__name__)

DEFAULT_ALIAS_PATH = Path(__file__).resolve().parents[1] / "data" / "game_aliases.json"
USER_ALIAS_FILENAME = "game_aliases.json"

_FUZZY_MIN_RATIO = 0.86   # 近似匹配阈值
_FUZZY_MIN_LEN = 5        # 输入过短不做近似匹配，避免误报
_CONTAIN_MIN_LEN = 4      # 双向包含要求较短一方至少这么长

# 版本后缀噪声：剥离后「鸣潮豪华版」与「鸣潮」等价
_EDITION_NOISE = (
    "豪华版", "终极版", "标准版", "典藏版", "年度版", "决定版", "重制版", "完全版",
    "deluxeedition", "ultimateedition", "definitiveedition", "standardedition",
    "collectorsedition", "completeedition", "gameoftheyear", "goty", "remastered",
)

_PUNCT_RE = re.compile(r"[\s\-_·:：,，.。!！?？'\"“”‘’()（）\[\]【】/\\|+]+")
_TRADEMARK_RE = re.compile(r"[™®©]")


@dataclass(frozen=True)
class GameIdentity:
    """一款游戏的规范身份。"""

    game_id: str
    names: Tuple[str, ...] = ()      # 官方名，中文优先
    aliases: Tuple[str, ...] = ()    # 俗称 / 缩写 / 变体

    @property
    def display_name(self) -> str:
        """面向用户的规范显示名（中文优先，无 names 时退回 game_id）。"""
        return self.names[0] if self.names else self.game_id


@dataclass(frozen=True)
class GameMatch:
    """一次名称解析的结果。

    :param method: ``exact`` / ``containment`` / ``fuzzy``；未命中为 ``none``
    :param matched: 命中的那个写法（便于审计与测试断言）
    """

    identity: Optional[GameIdentity] = None
    matched: str = ""
    method: str = "none"
    score: float = 0.0


def normalize_name(name: str) -> str:
    """归一化游戏名：折叠全角、去标点空格商标、剥离版本后缀、转小写。"""
    # 商标符号必须在 NFKC 之前剥离：NFKC 会把  展开成 "TM"、® 展开成 "R"，
    # 之后再删就删不干净了（Minecraft™ 会变成 minecrafttm）。
    s = _TRADEMARK_RE.sub("", name or "")
    s = unicodedata.normalize("NFKC", s).lower()
    s = _PUNCT_RE.sub("", s)
    changed = True
    while changed:
        changed = False
        for noise in _EDITION_NOISE:
            if s.endswith(noise) and len(s) > len(noise):
                s = s[: -len(noise)]
                changed = True
    return s


def _user_alias_path() -> Path:
    return app_dir() / USER_ALIAS_FILENAME


def _read_games(path: Path) -> Dict[str, GameIdentity]:
    """读取别名文件中的 ``games`` 段；文件缺失/损坏时返回空 dict（不阻断）。"""
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("游戏别名库读取失败，忽略该文件：%s", path, exc_info=True)
        return {}
    games = raw.get("games") if isinstance(raw, dict) else None
    if not isinstance(games, dict):
        return {}
    out: Dict[str, GameIdentity] = {}
    for game_id, entry in games.items():
        if not isinstance(entry, dict):
            continue
        names = tuple(str(n).strip() for n in entry.get("names", []) if str(n).strip())
        aliases = tuple(str(a).strip() for a in entry.get("aliases", []) if str(a).strip())
        if not names and not aliases:
            continue
        out[str(game_id)] = GameIdentity(str(game_id), names, aliases)
    return out


@dataclass(frozen=True)
class _AliasIndex:
    games: Dict[str, GameIdentity]
    variants: Dict[str, GameIdentity]   # 归一化写法 → 游戏身份


_INDEX_CACHE: Dict[str, _AliasIndex] = {}


def load_aliases(user_path: "Path | str | None" = None) -> _AliasIndex:
    """加载「包内主库 + 用户覆盖文件」并建归一化索引（进程内缓存）。

    :param user_path: 覆盖用户文件路径（默认 ``~/.aemeath_agent/game_aliases.json``），
        主要供测试注入临时文件。
    """
    key = str(user_path) if user_path is not None else str(_user_alias_path())
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    games = dict(_read_games(DEFAULT_ALIAS_PATH))
    user_games = _read_games(Path(key))
    if user_games:
        games.update(user_games)  # 同 game_id 整体覆盖
        logger.info("用户游戏别名库已合并：%d 条", len(user_games))

    variants: Dict[str, GameIdentity] = {}
    for identity in games.values():
        for variant in (*identity.names, *identity.aliases):
            norm = normalize_name(variant)
            if not norm:
                continue
            existing = variants.get(norm)
            if existing is not None and existing.game_id != identity.game_id:
                logger.debug(
                    "别名冲突：%r 同时属于 %s 与 %s，保留前者",
                    variant, existing.game_id, identity.game_id,
                )
                continue
            variants[norm] = identity

    index = _AliasIndex(games=games, variants=variants)
    _INDEX_CACHE[key] = index
    return index


def reset_cache() -> None:
    """清空别名索引缓存（测试或用户改完别名文件后手动刷新用）。"""
    _INDEX_CACHE.clear()


def resolve_game(name: str, user_path: "Path | str | None" = None) -> GameMatch:
    """把任意游戏名写法解析为规范身份。

    匹配顺序：精确 → 双向包含 → 近似。全部未命中时返回 ``method="none"``
    的空结果（调用方自行决定退回原名）。
    """
    query = normalize_name(name)
    if not query:
        return GameMatch()
    index = load_aliases(user_path)

    hit = index.variants.get(query)
    if hit is not None:
        return GameMatch(identity=hit, matched=name.strip(), method="exact", score=1.0)

    # 双向包含：覆盖「鸣潮启动闪退」这类夹带上下文、以及带副标题的写法
    if len(query) >= _CONTAIN_MIN_LEN:
        best: Tuple[float, Optional[GameIdentity], str] = (0.0, None, "")
        for variant, identity in index.variants.items():
            if len(variant) < _CONTAIN_MIN_LEN:
                continue
            if query in variant or variant in query:
                score = min(len(query), len(variant)) / max(len(query), len(variant))
                if score > best[0]:
                    best = (score, identity, variant)
        if best[1] is not None:
            return GameMatch(identity=best[1], matched=best[2],
                             method="containment", score=round(best[0], 3))

    # 近似匹配：容忍错拼（如 wuthering wave / cyberpunk207）
    if len(query) >= _FUZZY_MIN_LEN:
        best_ratio, best_identity, best_variant = 0.0, None, ""
        for variant, identity in index.variants.items():
            if len(variant) < _CONTAIN_MIN_LEN:
                continue
            ratio = SequenceMatcher(None, query, variant).ratio()
            if ratio > best_ratio:
                best_ratio, best_identity, best_variant = ratio, identity, variant
        if best_identity is not None and best_ratio >= _FUZZY_MIN_RATIO:
            return GameMatch(identity=best_identity, matched=best_variant,
                             method="fuzzy", score=round(best_ratio, 3))

    return GameMatch(matched=name.strip(), method="none")


def game_id(name: str, user_path: "Path | str | None" = None) -> str:
    """规范游戏标识；未收录返回空串。"""
    match = resolve_game(name, user_path)
    return match.identity.game_id if match.identity else ""


def display_name(name: str, user_path: "Path | str | None" = None) -> str:
    """规范显示名（中文优先）；未收录时原样返回去空白后的输入。"""
    match = resolve_game(name, user_path)
    if match.identity is not None:
        return match.identity.display_name
    return (name or "").strip()