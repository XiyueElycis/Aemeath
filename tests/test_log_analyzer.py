"""LogAnalyzerAgent 近 N 天日志扫描与上限截断测试。"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from gamedoctor.agents.base_agent import AgentTask
from gamedoctor.agents.logs.log_analyzer_agent import (
    _DEFAULT_LOOKBACK_DAYS,
    _MAX_FILES,
    LogAnalyzerAgent,
)

_DAY = 86400


def _make_log(root: Path, name: str, age_days: float) -> Path:
    """在 root 下造一个日志文件，并把 mtime 设为 age_days 天前。"""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("error: boom\n", encoding="utf-8")
    ts = time.time() - age_days * _DAY
    os.utime(path, (ts, ts))
    return path


def _agent() -> LogAnalyzerAgent:
    a = LogAnalyzerAgent()
    a.initialize()
    return a


def test_default_only_recent_three_days(tmp_path: Path) -> None:
    recent = _make_log(tmp_path, "recent.log", age_days=0.5)
    _make_log(tmp_path, "old.log", age_days=10)

    found = _agent()._discover_logs(str(tmp_path))

    assert found == [recent]


def test_newest_first_order(tmp_path: Path) -> None:
    older = _make_log(tmp_path, "older.log", age_days=2)
    newer = _make_log(tmp_path, "newer.log", age_days=0.1)

    found = _agent()._discover_logs(str(tmp_path))

    assert found == [newer, older]


def test_log_days_zero_means_unlimited(tmp_path: Path) -> None:
    recent = _make_log(tmp_path, "recent.log", age_days=1)
    old = _make_log(tmp_path, "old.log", age_days=30)

    found = _agent()._discover_logs(str(tmp_path), lookback_days=0)

    assert set(found) == {recent, old}
    assert found[0] == recent  # 仍按 mtime 倒序


def test_custom_lookback_window(tmp_path: Path) -> None:
    within = _make_log(tmp_path, "within.log", age_days=6)
    _make_log(tmp_path, "beyond.log", age_days=8)

    found = _agent()._discover_logs(str(tmp_path), lookback_days=7)

    assert found == [within]


def test_cap_keeps_newest_max_files(tmp_path: Path) -> None:
    total = _MAX_FILES + 5
    for i in range(total):
        _make_log(tmp_path, f"log_{i:02d}.log", age_days=0.01 + i * 0.001)

    found = _agent()._discover_logs(str(tmp_path))

    assert len(found) == _MAX_FILES
    # age_days 最小（i=0）的最新，应排在首位；最旧的 5 个（log_20~24）被截断
    assert found[0].name == "log_00.log"
    names = {p.name for p in found}
    assert "log_00.log" in names
    assert "log_24.log" not in names
    assert "log_20.log" not in names
    assert "log_19.log" in names


def test_non_log_files_ignored(tmp_path: Path) -> None:
    _make_log(tmp_path, "a.log", age_days=0)
    note = tmp_path / "readme.md"
    note.write_text("hello", encoding="utf-8")

    found = _agent()._discover_logs(str(tmp_path))

    assert [p.name for p in found] == ["a.log"]


def test_execute_list_respects_log_days(tmp_path: Path) -> None:
    _make_log(tmp_path, "fresh.log", age_days=1)
    _make_log(tmp_path, "stale.log", age_days=9)

    agent = _agent()

    # 缺省近 3 天：只命中 fresh
    task = AgentTask(name="t", data={"operation": "list", "game_dir": str(tmp_path)})
    result = asyncio.run(agent.execute(task))
    assert result.success
    assert result.data.files == [str(tmp_path / "fresh.log")]
    assert f"近 {_DEFAULT_LOOKBACK_DAYS} 天" in result.message

    # log_days=0：两个都命中
    task_all = AgentTask(
        name="t", data={"operation": "list", "game_dir": str(tmp_path), "log_days": 0}
    )
    result_all = asyncio.run(agent.execute(task_all))
    assert {Path(f).name for f in result_all.data.files} == {"fresh.log", "stale.log"}
    assert "不限日期" in result_all.message
