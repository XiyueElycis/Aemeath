"""日志解析 + 日志自动定位器（PRD §4.4 日志自动定位器）。

内置主流引擎 / 平台日志路径映射表，如 UE ``%LOCALAPPDATA%/{Game}/Saved/Logs``、
Unity ``output_log.txt``。映射表可增量维护。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from ..models import LogEntry

# --------------------------------------------------------------------------- #
# 日志路径映射表（可增量维护）
# --------------------------------------------------------------------------- #
# 值为模板字符串，支持 {game}、{user}、{localappdata} 占位符。
# 同一引擎可对应多条候选路径模板（如 UE 同时有安装目录与文档目录两处日志）。

LOG_PATH_MAP: dict[str, tuple[str, ...]] = {
    "unreal": (
        r"{localappdata}/{game}/Saved/Logs/*.log",
        r"{user}/Documents/{game}/Saved/Logs/*.log",
    ),
    "unity": (
        r"{game}/output_log.txt",
        # {localappdata} 展开为 "...\AppData\Local"，再拼接 "low" 得到 LocalLow；
        # Windows 路径不区分大小写，与下一行的正式写法等价。
        r"{localappdata}low/{game}/Player.log",
        r"{user}/AppData/LocalLow/{game}/Player.log",
    ),
    "godot": (
        r"{user}/AppData/Roaming/Godot/app_userdata/{game}/logs/*.log",
    ),
    "steam": (
        r"{steam}/steamapps/common/{game}/*.log",
    ),
}


class LogParser(Protocol):
    """日志解析器协议。"""

    def parse(self, path: Path) -> list[LogEntry]:
        """解析单个日志文件，返回结构化日志条目。"""
        ...


def _expand_template(tmpl: str, game: str) -> Path:
    """把路径模板里的占位符（``{game}``/``{user}``/``{localappdata}``/``{steam}``）
    替换为具体值，得到 Path。不做 glob（通配符留给调用方处理）。"""
    ctx = {
        "game": game,
        "user": str(Path.home()),
        "localappdata": os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")),
        "steam": os.environ.get("STEAM_PATH", str(Path.home() / ".steam")),
    }
    for k, v in ctx.items():
        # 用 replace 而非 str.format，避免模板/路径中出现的其它花括号被误当占位符
        tmpl = tmpl.replace("{" + k + "}", v)
    return Path(tmpl)


def locate_logs(engine: str, game: str) -> list[Path]:
    """按引擎返回候选日志路径（可能存在也可能不存在，由调用方判断）。

    :param engine: 引擎名，即 ``LOG_PATH_MAP`` 的键（如 "unreal" / "unity"）。
    :param game: 游戏名，用于填充模板中的 ``{game}`` 占位符。
    :return: 展开后的候选 ``Path`` 列表；含通配符的模板会展开成实际匹配到的文件。
    """
    out: list[Path] = []
    for tmpl in LOG_PATH_MAP.get(engine, ()):
        p = _expand_template(tmpl, game)
        # 支持简单 glob（文件名含 * 通配）：展开匹配到的实际文件；目录不存在则跳过
        if "*" in p.name:
            out.extend(sorted(p.parent.glob(p.name)) if p.parent.exists() else [])
        else:
            out.append(p)
    return out


class SimpleLogParser:
    """默认实现骨架：逐行读取，按常见级别关键词粗分。

    暂不做时间戳/调用栈的结构化解析，先用关键词命中给出粗略级别。
    """

    def parse(self, path: Path) -> list[LogEntry]:
        # 结果容器：逐行收集结构化日志条目
        entries: list[LogEntry] = []
        if not path.exists():
            return entries
        # TODO: 按引擎做更精细的日志格式解析（时间戳/级别/堆栈）
        # 逐行读取，按常见级别关键词粗分；errors="replace" 容忍编码损坏的日志
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            level = None
            low = line.lower()
            # 关键词从错误/致命/异常到警告排列，命中即停：严重级别优先于 warning
            for kw in ("error", "fatal", "exception", "warning", "warn"):
                if kw in low:
                    level = kw
                    break
            # line_no 取 1 起始行号（i + 1），便于用户对照原日志定位
            entries.append(LogEntry(source=path, line_no=i + 1, level=level, message=line))
        return entries


def parse_logs(paths: list[Path]) -> list[LogEntry]:
    """批量解析多条日志路径，合并为单个结构化条目列表。"""
    parser = SimpleLogParser()
    out: list[LogEntry] = []
    for p in paths:
        out.extend(parser.parse(p))
    return out
