"""文本内容的编辑算法层（PRD §4.5.1 文件级 / 配置级）。

与 :mod:`gamedoctor.textfile` 的分工：

- ``textfile`` 负责**怎么把一个文本文件安全地读出来、原样写回去**（编码 / 行尾 / BOM）；
- 本模块负责**内容层面怎么改**：字面量替换、正则替换、ini / json / kv 结构化改值。

之所以不直接在原语里写这些，是因为它们是"与具体游戏无关、但每种文本改法都要
不同实现"的算法，抽出来既能被多个原语复用，也能单独测试。

结构化改写一律**按行精改**，不用 configparser：

    configparser 写回会丢注释、重排键、改缩进——玩家精心注释过的配置被重写
    等于被破坏。游戏圈最常见的 ini（UE 的 ``*Settings.ini``）、kv（Source 的
    ``autoexec.cfg``、Minecraft 的 ``options.txt``）都需要保排版。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..errors import TextEditError

# 后缀 → 结构化格式。未知后缀按 kv 行式处理（覆盖 .cfg / .txt / .properties 等）
_SUFFIX_FORMATS = {".json": "json", ".jsonc": "json", ".ini": "ini"}


# --------------------------------------------------------------------------- #
# 自由文本替换
# --------------------------------------------------------------------------- #


def replace_literal(text: str, old: str, new: str, *, count: int = 0,
                    expect: int | None = None) -> tuple[str, int]:
    """字面量替换，返回 ``(新文本, 实际替换处数)``。

    :param count: 最多替换几处，``0`` 表示全部替换。
    :param expect: 期望 ``old`` 在文中出现的次数；与实际不符时抛
        :class:`TextEditError` 拒绝改写——这是防止"改错文件 / 锚点漂移"的关键闸门。
    """
    if not old:
        raise TextEditError("缺少替换锚点 old（空串无法定位）")
    occurrences = text.count(old)
    if expect is not None and occurrences != expect:
        raise TextEditError(
            f"锚点出现 {occurrences} 次，与期望的 {expect} 次不符，拒绝改写"
        )
    if count and count > 0:
        return text.replace(old, new, count), min(count, occurrences)
    return text.replace(old, new), occurrences


def replace_regex(text: str, pattern: str, repl: str, *, count: int = 0,
                  expect: int | None = None, flags: int = 0) -> tuple[str, int]:
    """正则替换，返回 ``(新文本, 实际替换处数)``；参数语义同 :func:`replace_literal`。

    ``repl`` 支持 ``\\1`` / ``\\g<name>`` 反向引用。
    """
    if not pattern:
        raise TextEditError("缺少正则表达式 pattern")
    regex = re.compile(pattern, flags)
    occurrences = len(list(regex.finditer(text)))
    if expect is not None and occurrences != expect:
        raise TextEditError(
            f"正则命中 {occurrences} 处，与期望的 {expect} 处不符，拒绝改写"
        )
    if count and count > 0:
        new_text, n = regex.subn(repl, text, count=count)
    else:
        new_text, n = regex.subn(repl, text)
    return new_text, n


# --------------------------------------------------------------------------- #
# 结构化改值：ini / json / kv（均按行精改，保留注释与排版）
# --------------------------------------------------------------------------- #


def detect_format(path: Path, fmt: str = "auto") -> str:
    """判定结构化格式：``auto`` 时按后缀猜（未知后缀一律按 kv 行式处理）。"""
    if fmt and fmt != "auto":
        return fmt
    return _SUFFIX_FORMATS.get(Path(path).suffix.lower(), "kv")


def _locate_ini_section(lines: list[str], section: str | None) -> tuple[int | None, int, bool]:
    """定位 ini 小节，返回 ``(正文起始行, 正文结束行, 小节是否已存在)``。

    - ``section`` 为空 → 视为"第一个小节之前的部分"，即全文（0, len）；
    - 找到同名 ``[section]`` → 从其后一行到下一个小节头（或文末）；
    - 未找到 → ``(None, len(lines), False)``，由调用方决定是否追加新小节。
    """
    if not section:
        return 0, len(lines), True
    header = re.compile(r"^\s*\[\s*(.*?)\s*\]\s*$")
    start: int | None = None
    for i, line in enumerate(lines):
        matched = header.match(line)
        if not matched:
            continue
        if start is not None:                      # 已命中目标小节，遇到下一个小节头即结束
            return start, i, True
        if matched.group(1).lower() == section.lower():
            start = i + 1
    if start is None:
        return None, len(lines), False
    return start, len(lines), True


def _build_kv_pattern(key: str, sep: str) -> re.Pattern[str]:
    """构造匹配 ``key<sep>value`` 的行正则。

    容忍三种真实世界的写法：行首空白、被注释掉的同名键（``;key=value``）、
    ``=`` 前后的任意空格。分组：1=前缀、2=键、3=分隔符含两侧空格、4=原值。
    """
    return re.compile(
        rf"^(\s*[;#]*\s*)({re.escape(key)})(\s*{re.escape(sep)}\s*)(.*)$",
        re.IGNORECASE,
    )


def set_kv_value(text: str, key: str, value: Any, *, sep: str = "=",
                 add_if_missing: bool = True) -> tuple[str, int]:
    """按行改写 ``key<sep>value`` 形式的配置，返回 ``(新文本, 改动处数)``。

    只动命中的那一行，注释、空行、其余键原样保留。键不存在时：
    ``add_if_missing`` 为真则在文末追加 ``key<sep>value``，否则原样返回、改动 0 处。
    """
    if not key:
        raise TextEditError("缺少配置项键名 key")
    lines = text.split("\n")
    pattern = _build_kv_pattern(key, sep)
    for i, line in enumerate(lines):
        matched = pattern.match(line)
        if matched:
            # 保留原前缀（含注释符）与分隔符写法，只替换值
            lines[i] = f"{matched.group(1)}{matched.group(2)}{matched.group(3)}{value}"
            return "\n".join(lines), 1
    if add_if_missing:
        lines.append(f"{key}{sep}{value}")
        return "\n".join(lines), 1
    return text, 0


def set_ini_value(text: str, key: str, value: Any, *, section: str | None = None,
                  add_if_missing: bool = True) -> tuple[str, int]:
    """改写 ini 指定小节下的键值，返回 ``(新文本, 改动处数)``。

    小节不存在且允许追加时，在文末补 ``[section]`` + 键值两行。
    """
    lines = text.split("\n")
    start, end, _section_exists = _locate_ini_section(lines, section)
    if start is None:
        if not add_if_missing:
            return text, 0
        if lines and lines[-1].strip():            # 文末非空行则插入一个空行分隔
            lines.append("")
        lines.extend([f"[{section}]", f"{key}={value}"])
        return "\n".join(lines), 1

    pattern = _build_kv_pattern(key, "=")
    for i in range(start, end):
        matched = pattern.match(lines[i])
        if matched:
            lines[i] = f"{matched.group(1)}{matched.group(2)}{matched.group(3)}{value}"
            return "\n".join(lines), 1
    if add_if_missing:
        lines.insert(end, f"{key}={value}")
        return "\n".join(lines), 1
    return text, 0


def _coerce(raw: Any) -> Any:
    """把 LLM/用户输入的值尝试还原成 JSON 标量（"30"→30、"true"→True）。

    配置里写的是字符串，但语义上可能是数字或布尔；这里做保守推断，
    无法判定（如 "1.2.3"）时保持原字符串。
    """
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _set_json_path(data: Any, pointer: str, value: Any) -> Any:
    """按点分路径（``graphics.fps``）写入嵌套 dict，返回写后的根对象。"""
    parts = [p for p in pointer.split(".") if p]
    if not parts:
        raise TextEditError("缺少 JSON 键路径 key")
    node = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise TextEditError(f"JSON 路径不存在: {pointer}")
        node = node[part]
    if not isinstance(node, dict):
        raise TextEditError(f"JSON 路径末端不是对象: {pointer}")
    node[parts[-1]] = _coerce(value)
    return data


def _get_json_path(data: Any, pointer: str) -> Any:
    """按点分路径读取 JSON 值；任一层缺失返回 None。"""
    node = data
    for part in [p for p in pointer.split(".") if p]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_json_value(text: str, pointer: str, value: Any, *,
                   indent: int | None = None) -> tuple[str, int]:
    """改写 JSON 顶层/嵌套键值，返回 ``(新文本, 改动处数)``。

    注：JSON 走「解析—改值—重新序列化」，会统一缩进与键顺序，无法像 ini/kv
    那样保排版（JSON 本身也无注释可丢）。
    """
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        raise TextEditError(f"目标不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TextEditError("JSON 根节点不是对象，无法按键改值")
    data = _set_json_path(data, pointer, value)
    new_text = json.dumps(data, ensure_ascii=False, indent=indent)
    return new_text, 0 if new_text == text else 1


def read_json_value(text: str, pointer: str) -> str | None:
    """读取 JSON 指定路径的值，返回字符串形式；不存在返回 None。"""
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return None
    value = _get_json_path(data, pointer)
    return None if value is None else str(value)


def set_config_value(text: str, key: str, value: Any, *, fmt: str = "kv",
                     section: str | None = None, sep: str = "auto",
                     add_if_missing: bool = True) -> tuple[str, int]:
    """按格式分发的统一入口：``ini`` / ``json`` / ``kv``，返回 ``(新文本, 改动处数)``。"""
    if fmt == "json":
        return set_json_value(text, key, value)
    if fmt == "ini":
        return set_ini_value(text, key, value, section=section,
                             add_if_missing=add_if_missing)
    if sep and sep != "auto":
        return set_kv_value(text, key, value, sep=sep, add_if_missing=add_if_missing)
    # sep="auto"：先按 = 试，没命中再按 : 试（Minecraft options.txt 用冒号）
    new_text, hits = set_kv_value(text, key, value, sep="=", add_if_missing=False)
    if hits:
        return new_text, hits
    return set_kv_value(text, key, value, sep=":", add_if_missing=add_if_missing)


def read_config_value(text: str, key: str, *, fmt: str = "kv",
                      section: str | None = None, sep: str = "auto") -> str | None:
    """读回配置项的当前值，返回字符串形式；未找到返回 None（供 verify 检查点用）。"""
    if fmt == "json":
        return read_json_value(text, key)
    lines = text.split("\n")
    if fmt == "ini":
        start, end, _section_exists = _locate_ini_section(lines, section)
        if start is None:
            return None
        candidate_indexes = range(start, end)
    else:
        candidate_indexes = range(len(lines))
    for sep_char in ((sep,) if sep != "auto" else ("=", ":")):
        pattern = _build_kv_pattern(key, sep_char)
        for i in candidate_indexes:
            matched = pattern.match(lines[i])
            if matched:
                return matched.group(4).strip()
    return None
