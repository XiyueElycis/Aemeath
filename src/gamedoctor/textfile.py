"""文本文件的安全读写底座（编码 / 行尾 / BOM 保真 + 内容预览）。

放在与分层无关的位置（非 analyzer / 非 fixer），因为它是**多个层都要用的公共能力**：

- ② 信息采集层：给 LLM 提供"待改文件的真实内容"以生成准确的替换锚点；
- ⑤ 自动修复层：原语改完后要按原格式写回。

为什么需要它，而不是直接 ``path.read_text()``：

1. **编码多样**：UE 的 ini 常是 UTF-16（带 BOM）、国内游戏配置常见 GBK、现代
   工具写 UTF-8（可能带 BOM）。按错误的编码读或写，游戏会直接读不出配置。
2. **行尾混杂**：跨平台搬运的配置文件里 CRLF / LF / CR 可能同时出现。
3. **末尾换行**：有些文件末尾刻意不带换行，写回时凭空补一个会污染 diff。

:class:`TextDoc` 一次性解决这三点：**加载时嗅探，写回时还原**。
"""

from __future__ import annotations

import codecs
import difflib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import TextEditError

# BOM -> 对应 codec 名。顺序敏感：UTF-32 LE 的 BOM 以 UTF-16 LE 的 BOM 开头，
# 必须先判长 BOM，否则 UTF-32 文件会被误判成 UTF-16。
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# 无 BOM 时的解码候选顺序：UTF-8 → GBK（中文环境常见）→ UTF-16 → latin-1（兜底永不失败）
_FALLBACK_ENCODINGS = ("utf-8", "gbk", "utf-16", "latin-1")


def _sniff_bom(raw: bytes) -> tuple[str, bytes]:
    """按 BOM 判断编码，返回 ``(编码名, 去掉 BOM 后的字节)``；无 BOM 返回 ``("", raw)``。"""
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return enc, raw[len(bom):]
    return "", raw


def _guess_encoding(raw: bytes) -> str:
    """无 BOM 时按候选顺序试探：第一个能成功解码的编码即为结果。"""
    for enc in _FALLBACK_ENCODINGS:
        try:
            raw.decode(enc)
        except UnicodeDecodeError:
            continue
        return enc
    return "latin-1"  # pragma: no cover - latin-1 理论上永不失败


def detect_encoding(path: Path) -> str:
    """读写前嗅探文件编码（含 BOM 判断）。"""
    if not path.is_file():
        raise TextEditError(f"文本文件不存在: {path}")
    raw = path.read_bytes()
    enc, body = _sniff_bom(raw)
    return enc or _guess_encoding(body)


def detect_newline(text: str) -> str:
    """统计三种行尾出现次数，返回占比最高的作为"主行尾"。

    空文件或完全无换行时返回 ``"\\n"``。
    """
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    if crlf == 0 and cr == 0 and lf == 0:
        return "\n"
    return max((crlf, "\r\n"), (lf, "\n"), (cr, "\r"), key=lambda item: item[0])[1]


@dataclass
class TextDoc:
    """已加载的文本文件快照，携带写回所需的"原格式不变式"。

    属性：
        path: 文件绝对路径。
        text: 正文。**内部一律用 ``\\n`` 作行尾**，便于上层做按行处理。
        encoding: 读文件时用的编码；写回时用同一编码，BOM 由 codec 自动补回。
        newline: 原文件主行尾，写回时还原。
        trailing_newline: 原文件末尾是否带换行；不带的写回后也不补。
    """

    path: Path
    text: str
    encoding: str = "utf-8"
    newline: str = "\n"
    trailing_newline: bool = True

    @classmethod
    def load(cls, path: Path, encoding: str | None = None) -> TextDoc:
        """加载文本文件；``encoding`` 留空则自动嗅探（推荐，保真度最高）。"""
        if not path.is_file():
            raise TextEditError(f"文本文件不存在: {path}")
        raw = path.read_bytes()
        if encoding:
            enc, body = encoding, raw
        else:
            enc, body = _sniff_bom(raw)
            enc = enc or _guess_encoding(body)
        try:
            text = body.decode(enc)
        except UnicodeDecodeError as exc:  # pragma: no cover - 嗅探已保证可解码
            raise TextEditError(f"无法按 {enc} 解码文件: {path}") from exc

        return cls(
            path=path,
            text=text.replace("\r\n", "\n").replace("\r", "\n"),
            encoding=enc,
            newline=detect_newline(text),
            trailing_newline=text.endswith(("\n", "\r")),
        )

    def write(self, text: str | None = None) -> None:
        """落盘：还原编码 / 行尾 / 末尾换行状态。

        采用「同目录临时文件 + ``os.replace``」原子写，写到一半崩溃不会留下
        被截断的配置半成品。
        """
        if text is not None:
            self.text = text
        out = self.text.replace("\n", self.newline)
        ends_with_nl = out.endswith(("\n", "\r"))
        if self.trailing_newline and not ends_with_nl:
            out += self.newline
        elif not self.trailing_newline and ends_with_nl:
            out = out[: -len(self.newline)]

        data = out.encode(self.encoding)
        # 临时文件必须与目标同目录（同卷），os.replace 才是原子替换
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".gd-edit-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, str(self.path))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def unified_diff(before: str, after: str, *, label: str = "", context: int = 2,
                 max_lines: int = 40) -> str:
    """生成 before→after 的统一 diff 文本（dry-run 预览与执行留痕共用）。

    超过 ``max_lines`` 行会截断并追加提示，避免整份长配置淹没终端输出。
    """
    lines = list(
        difflib.unified_diff(
            before.split("\n"),
            after.split("\n"),
            fromfile=f"修改前 {label}".strip(),
            tofile=f"修改后 {label}".strip(),
            lineterm="",
            n=context,
        )
    )
    if not lines:
        return ""
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... 省略 {len(lines) - max_lines} 行 diff ..."]
    return "\n".join(lines)


def read_text_preview(path: Path, *, max_chars: int = 2000,
                      encoding: str | None = None) -> str:
    """读取文本文件前 ``max_chars`` 个字符，供 LLM / 报告查看，**只读不改**。

    返回内容带一行文件元信息（编码 / 行尾 / 是否被截断），方便上层判断：
    例如发现是 UTF-16 就该提醒用户这是 UE 原生配置。
    """
    doc = TextDoc.load(path, encoding)
    truncated = len(doc.text) > max_chars
    newline_name = {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}.get(doc.newline, "unknown")
    head = (
        f"# 文件: {path}  编码: {doc.encoding}  行尾: {newline_name}  "
        f"总字符: {len(doc.text)}{'（下方已截断）' if truncated else ''}\n"
    )
    return head + doc.text[:max_chars] + ("\n... (已截断)" if truncated else "")
