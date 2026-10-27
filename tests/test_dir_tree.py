"""目录巡检（analyzer.tree）+ 引擎下沉识别（fingerprint.engine）单测。

覆盖两件事：

1. :func:`scan_tree` 的只读有界递归：计数 / 体积 / 深度上限 / 数量上限截断；
2. ``SimpleEngineDetector.identify`` 从「只看顶层」改为「有界下钻」后，
   UE 这类把特征文件放在子目录里的游戏能被识别，且顶层命中仍优先。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aemeath_agent import context_builder as cb
from aemeath_agent.analyzer.tree import TreeInventory, scan_tree
from aemeath_agent.fingerprint.engine import SimpleEngineDetector
from aemeath_agent.models import PlatformInfo, RuntimeFingerprint
from aemeath_agent.runtime import get_tracker

MIB = 1024 * 1024


# --------------------------------------------------------------------------- #
# scan_tree
# --------------------------------------------------------------------------- #


def _make_tree(root: Path) -> Path:
    """root/{big.bin(2MiB), A/{a.txt, B/b.txt}}"""
    (root / "A" / "B").mkdir(parents=True)
    (root / "big.bin").write_bytes(b"x" * (2 * MIB))
    (root / "A" / "a.txt").write_bytes(b"aaa")
    (root / "A" / "B" / "b.txt").write_bytes(b"b")
    return root


def test_scan_tree_counts(tmp_path: Path):
    inv = scan_tree(_make_tree(tmp_path))

    assert isinstance(inv, TreeInventory)
    assert inv.root == str(tmp_path)
    assert inv.total_dirs == 2                     # A、A/B（根目录本身不算）
    assert inv.file_count == 3
    assert inv.size_mb == 2                        # 2MiB + 4B 取整
    assert inv.top_files == ("big.bin",)
    assert inv.truncated is False

    by_rel = {d.rel_path: d for d in inv.dirs}
    assert by_rel["A"].depth == 1
    assert by_rel["A"].file_count == 1             # 直属 a.txt
    assert by_rel["A"].subdir_count == 1           # 直属 B
    assert by_rel["A"].total_files == 2            # a.txt + B/b.txt
    assert by_rel["A"].total_size_mb == 0
    assert by_rel["A/B"].depth == 2
    assert by_rel["A/B"].file_count == 1
    assert by_rel["A/B"].subdir_count == 0

    assert [d.rel_path for d in inv.top_level()] == ["A"]
    assert [d.rel_path for d in inv.children(by_rel["A"])] == ["A/B"]
    assert inv.children(by_rel["A/B"]) == ()


def test_scan_tree_depth_limit(tmp_path: Path):
    """max_depth=1：只列顶层子目录，不再往下展开（深度边界不算截断）。"""
    inv = scan_tree(_make_tree(tmp_path), max_depth=1)

    assert [d.rel_path for d in inv.dirs] == ["A"]
    assert inv.file_count == 2                     # big.bin + A/a.txt
    assert inv.truncated is False


def test_scan_tree_dir_cap_truncates(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"d{i}").mkdir()
    inv = scan_tree(tmp_path, max_dirs=3)

    assert inv.truncated is True
    assert 0 < inv.total_dirs < 5


def test_scan_tree_file_cap_truncates(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_bytes(b"x")
    inv = scan_tree(tmp_path, max_files=2)

    assert inv.truncated is True
    assert inv.file_count == 2


def test_scan_tree_missing_dir_is_empty():
    """路径不存在 / 为 None：返回空清单，绝不抛异常。"""
    for bad in (None, "", Path("no/such/dir/at/all")):
        inv = scan_tree(bad)
        assert inv.dirs == ()
        assert inv.file_count == 0
        assert inv.size_mb == 0
        assert inv.truncated is False


def test_scan_tree_to_dict_is_json_ready(tmp_path: Path):
    inv = scan_tree(_make_tree(tmp_path))
    json.dumps(inv.to_dict(), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 引擎下沉识别
# --------------------------------------------------------------------------- #


def test_engine_detected_in_subdir(tmp_path: Path):
    """UE 特征在 Client/Binaries/Win64 下，旧实现（只看顶层）识别不到。"""
    win64 = tmp_path / "Client" / "Binaries" / "Win64"
    win64.mkdir(parents=True)
    (win64 / "Client-Win64-Shipping.exe").write_bytes(b"MZ")

    fp = SimpleEngineDetector().identify(tmp_path)

    assert fp is not None
    assert fp.engine == "unreal"
    assert fp.confidence == 0.8


def test_engine_top_level_wins(tmp_path: Path):
    """顶层与深层同时命中：BFS 保证顶层优先（unity 胜出）。"""
    (tmp_path / "UnityPlayer.dll").write_bytes(b"x")
    win64 = tmp_path / "Client" / "Binaries" / "Win64"
    win64.mkdir(parents=True)
    (win64 / "Client-Win64-Shipping.exe").write_bytes(b"MZ")

    fp = SimpleEngineDetector().identify(tmp_path)

    assert fp is not None
    assert fp.engine == "unity"


def test_engine_beyond_depth_not_matched(tmp_path: Path):
    """超出扫描深度（第 4 层）的特征文件不参与识别。"""
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "Deep-Shipping.exe").write_bytes(b"MZ")

    assert SimpleEngineDetector().identify(tmp_path) is None


def test_engine_none_for_plain_dir(tmp_path: Path):
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    assert SimpleEngineDetector().identify(tmp_path) is None


# --------------------------------------------------------------------------- #
# build_context 集成：清单落盘 + 运行监控「目录级 + 聚合」上报
# --------------------------------------------------------------------------- #


@pytest.fixture
def _isolate(monkeypatch):
    """隔离机器相关探测：不跑 PowerShell / nvidia-smi / 平台库扫描。"""
    cb.probe_gpu_vram_mb.cache_clear()
    monkeypatch.setattr(cb, "cached_runtime", lambda: RuntimeFingerprint(os="Windows 11"))
    monkeypatch.setattr(cb, "probe_gpu_vram_mb", lambda gpu_name=None: None)
    monkeypatch.setattr(
        cb, "detect_platform",
        lambda name, root=None: PlatformInfo(platform="standalone", install_path=root),
    )
    yield
    getattr(cb.probe_gpu_vram_mb, "cache_clear", lambda: None)()


def _make_ue_game(root: Path) -> Path:
    """root/{Client/Binaries/Win64/*-Shipping.exe, Engine/Binaries/Win64/}"""
    (root / "Client" / "Binaries" / "Win64").mkdir(parents=True)
    (root / "Client" / "Binaries" / "Win64" / "Game-Win64-Shipping.exe").write_bytes(b"MZ")
    (root / "Engine" / "Binaries" / "Win64").mkdir(parents=True)
    (root / "launcher.exe").write_bytes(b"MZ")
    return root


def test_build_context_inventory_and_events(tmp_path: Path, _isolate):
    root = _make_ue_game(tmp_path / "Wuthering Waves Game")
    before = get_tracker().snapshot()["events"]

    ctx, fp = cb.build_context("wuthering_waves", str(root))

    # 引擎识别已下沉到子目录
    assert ctx.engine == "unreal"
    # 完整清单进 GameContext
    rels = [d["rel_path"] for d in ctx.dir_summary]
    assert "Client" in rels
    assert "Client/Binaries/Win64" in rels
    # 跨边界的浅两层清单进指纹 dict，且可序列化
    assert fp["tree_dir_count"] == len(ctx.dir_summary)
    assert fp["tree_file_count"] == 2
    assert all(d["depth"] <= 2 for d in fp["tree_dirs"])
    json.dumps(fp, ensure_ascii=False)

    # 运行监控：根一行 + 每个顶层子目录一行（聚合子目录）+ 汇总一行
    after = get_tracker().snapshot()["events"]
    new = after[: len(after) - len(before)]
    access = [e for e in new if e["kind"] == "access"]
    text = "\n".join(e["message"] for e in access)
    assert "正在巡检目录" in text
    assert "正在访问目录：Client" in text
    assert "含 Binaries" in text           # 顶层子目录聚合了它的下级
    assert "正在访问目录：Engine" in text
    assert "巡检完成" in text
    # 路径指向子目录而非只有根
    assert any(e["path"].endswith("Client") for e in access)


def test_build_context_without_dir_reports_nothing(_isolate):
    before = get_tracker().snapshot()["events"]

    ctx, fp = cb.build_context("随便聊聊", "")

    assert ctx.dir_summary == []
    assert "tree_dir_count" not in fp
    after = get_tracker().snapshot()["events"]
    assert len(after) == len(before)