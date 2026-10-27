# -*- coding: utf-8 -*-
"""游戏名称匹配知识库单测。

覆盖：
- normalize_name：全角折叠、标点/空格/商标剥离、版本后缀剥离；
- resolve_game：归一化精确 → 双向包含 → 近似 三级匹配，短别名不参与
  包含/近似匹配（防 ``ww``/``mc`` 误报），未收录回退 none；
- 用户覆盖文件：合并主库、同 game_id 整体覆盖；
- display_name / game_id 回退；
- 索引缓存与 reset_cache；
- context_builder 与 coordinator 的身份接线（指纹与显示口径统一）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aemeath_agent.governance.coordinator import _game_display
from aemeath_agent.knowledge import game_names as gn
from aemeath_agent.models import GameContext, TechStackFingerprint


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清缓存，避免用例间互相污染。"""
    gn.reset_cache()
    yield
    gn.reset_cache()


def _write_user_aliases(path: Path, games: dict) -> Path:
    path.write_text(
        json.dumps({"version": 1, "games": games}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Wuthering Waves", "wutheringwaves"),
        ("\uff37\uff35\uff34\uff28\uff25\uff32\uff29\uff2e\uff27\u3000\uff37\uff21\uff36\uff25\uff33",
         "wutheringwaves"),   # 全角字母 + 全角空格
        ("GTA V", "gtav"),
        ("GTA5", "gta5"),
        ("Minecraft™", "minecraft"),
        ("崩坏：星穹铁道", "崩坏星穹铁道"),
        ("  ELDEN RING  ", "eldenring"),
        ("Assassin's Creed Valhalla", "assassinscreedvalhalla"),
    ],
)
def test_normalize_basic(raw, expected):
    assert gn.normalize_name(raw) == expected


def test_normalize_strips_edition_suffix():
    assert gn.normalize_name("鸣潮豪华版") == "鸣潮"
    assert gn.normalize_name("黑神话：悟空终极版") == "黑神话悟空"
    assert gn.normalize_name("Elden Ring Deluxe Edition") == "eldenring"
    assert gn.normalize_name("Wuthering Waves GOTY") == "wutheringwaves"


def test_normalize_empty():
    assert gn.normalize_name("") == ""
    assert gn.normalize_name(None) == ""


# --------------------------------------------------------------------------- #
# 三级匹配
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    ["Wuthering Waves", "wuthering waves", "鸣潮", "WUTHERING  WAVES"],
)
def test_resolve_exact(raw):
    match = gn.resolve_game(raw)
    assert match.method == "exact"
    assert match.identity is not None
    assert match.identity.game_id == "wuthering_waves"
    assert match.identity.display_name == "鸣潮"
    assert match.score == 1.0


@pytest.mark.parametrize(
    "raw,expected_id",
    [
        ("wuwa", "wuthering_waves"),          # 短别名走精确匹配是允许的
        ("mc", "minecraft"),
        ("gta5", "grand_theft_auto_v"),
        ("GTA v", "grand_theft_auto_v"),
        ("给他爱5", "grand_theft_auto_v"),
        ("星铁", "honkai_star_rail"),
        ("zzz", "zenless_zone_zero"),
        ("老头环", "elden_ring"),
        ("天际", "the_elder_scrolls_v_skyrim"),
        ("天际线", "cities_skylines"),
    ],
)
def test_resolve_alias(raw, expected_id):
    match = gn.resolve_game(raw)
    assert match.method == "exact"
    assert match.identity is not None
    assert match.identity.game_id == expected_id


def test_containment_with_surrounding_context():
    """夹带上下文的写法（如日志/目录名后缀）靠双向包含兜住。"""
    match = gn.resolve_game("wuthering waves crash log")
    assert match.method == "containment"
    assert match.identity is not None
    assert match.identity.game_id == "wuthering_waves"


def test_fuzzy_typo():
    match = gn.resolve_game("wuthering wavez")
    assert match.method == "fuzzy"
    assert match.identity is not None
    assert match.identity.game_id == "wuthering_waves"
    assert match.score >= gn._FUZZY_MIN_RATIO


def test_short_variants_do_not_participate_in_loose_matching():
    """两个字符的写法既不做包含也不做近似：宁可判未知，也不猜。"""
    assert gn.resolve_game("ww").method == "none"
    assert gn.resolve_game("ww").identity is None
    assert gn.resolve_game("崩坏").method != "exact"   # 短中文名不做包含匹配


def test_unknown_game_falls_back_to_none():
    match = gn.resolve_game("Stellaris")
    assert match.method == "none"
    assert match.identity is None
    assert match.matched == "Stellaris"


@pytest.mark.parametrize("raw", ["", "   ", "!!!", None])
def test_empty_query(raw):
    match = gn.resolve_game(raw)
    assert match.method == "none"
    assert match.identity is None


# --------------------------------------------------------------------------- #
# 显示名 / game_id
# --------------------------------------------------------------------------- #
def test_display_name_and_game_id():
    assert gn.display_name("wuthering_waves") == "鸣潮"
    assert gn.display_name("Wuthering Waves") == "鸣潮"
    assert gn.game_id("鸣潮") == "wuthering_waves"
    # 未收录：显示名原样返回，game_id 为空串
    assert gn.display_name("Stellaris") == "Stellaris"
    assert gn.game_id("Stellaris") == ""


def test_identity_display_name_falls_back_to_game_id():
    assert gn.GameIdentity("custom_game").display_name == "custom_game"
    assert gn.GameIdentity("custom_game", names=("群星",)).display_name == "群星"


# --------------------------------------------------------------------------- #
# 用户覆盖文件
# --------------------------------------------------------------------------- #
def test_user_file_adds_new_game(tmp_path: Path):
    user = _write_user_aliases(tmp_path / "game_aliases.json", {
        "stellaris": {"names": ["群星", "Stellaris"], "aliases": ["stellar"]},
    })

    match = gn.resolve_game("群星", user_path=user)
    assert match.identity is not None
    assert match.identity.game_id == "stellaris"

    index = gn.load_aliases(user)
    assert "stellaris" in index.games
    assert "wuthering_waves" in index.games   # 主库仍在


def test_user_file_overrides_same_game_id(tmp_path: Path):
    user = _write_user_aliases(tmp_path / "game_aliases.json", {
        "wuthering_waves": {"names": ["Wuthering Waves", "鸣潮"], "aliases": ["wuwa"]},
    })

    # 用户文件的 names 顺序优先：显示名整体被覆盖
    match = gn.resolve_game("鸣潮", user_path=user)
    assert match.identity is not None
    assert match.identity.display_name == "Wuthering Waves"
    assert gn.display_name("wuwa", user_path=user) == "Wuthering Waves"


def test_user_file_missing_or_broken_is_ignored(tmp_path: Path):
    missing = tmp_path / "nope.json"
    assert gn.game_id("鸣潮", user_path=missing) == "wuthering_waves"

    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert gn.game_id("鸣潮", user_path=broken) == "wuthering_waves"

    # games 段结构不对同样不阻断
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"games": ["鸣潮"]}), encoding="utf-8")
    assert gn.game_id("鸣潮", user_path=weird) == "wuthering_waves"


def test_alias_conflict_keeps_first(tmp_path: Path):
    """同一写法被两处声明时保留先注册者，不抛异常。"""
    user = _write_user_aliases(tmp_path / "game_aliases.json", {
        "fake_minecraft": {"names": ["麦块"], "aliases": []},
    })
    index = gn.load_aliases(user)
    first = index.variants[gn.normalize_name("麦块")]
    assert first.game_id == "minecraft"


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def test_index_cache_and_reset(tmp_path: Path):
    user = tmp_path / "game_aliases.json"   # 不存在：只用主库
    first = gn.load_aliases(user)
    assert gn.load_aliases(user) is first

    gn.reset_cache()
    assert gn.load_aliases(user) is not first


# --------------------------------------------------------------------------- #
# 接线：指纹与显示口径统一
# --------------------------------------------------------------------------- #
def test_fingerprint_dict_carries_identity():
    from aemeath_agent.context_builder import fingerprint_to_dict

    fp = TechStackFingerprint(game_name="wuthering_waves")
    out = fingerprint_to_dict(fp, "X:/Games/wuthering_waves", None,
                              "wuthering_waves", "鸣潮")
    json.dumps(out, ensure_ascii=False)
    assert out["game_name_input"] == "wuthering_waves"
    assert out["game_id"] == "wuthering_waves"
    assert out["game_display_name"] == "鸣潮"


def test_coordinator_game_display():
    assert _game_display("X:/Games/wuthering_waves") == "鸣潮"
    assert _game_display("X:/Games/Wuthering Waves") == "鸣潮"
    assert _game_display("") == "Unknown"
    assert _game_display("", fallback="未知") == "未知"
    # 未收录：回退目录名，绝不丢信息
    assert _game_display("X:/Games/Stellaris") == "Stellaris"


def test_game_context_identity_fields_default_empty():
    ctx = GameContext(game_name="Stellaris")
    assert ctx.game_id == ""
    assert ctx.game_display_name == ""