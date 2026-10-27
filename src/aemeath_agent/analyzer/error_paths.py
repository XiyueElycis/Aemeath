"""报错文本 → 被引用路径的提取与针对性只读核查。

日志智能体原先只摘报错行做文字总结，不会顺着报错里出现的路径去现场核查。
本模块补上「证据闭环」的前半段（纯逻辑，无 LLM、无写操作）：

1. :func:`extract_referenced_paths` 从报错行中提取路径候选
   （引号包裹串 / Windows 绝对路径 / Unix 绝对路径 / 带分隔符且末段像
   文件名的相对路径），剔除 URL 与行尾标点，按大小写不敏感去重；
2. :func:`check_referenced_paths` 把候选锚定到游戏根目录做边界校验
   （根外路径标记 ``out_of_scope`` 且**不访问**），再核查存在性 / 是否目录 /
   0 字节 / 同目录兄弟文件数——沙箱模式下读取经 overlay 映射，与智能体
   统一的 ``sandbox.io`` 口径一致。

设计原则：**宁漏勿误报**。无分隔符的裸文件名、没有扩展名的相对 token
不提取（``missing`` 结论会直接面向用户，假阳性代价高）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

PathLike = Union[str, Path]

# 路径段：严格段不含空格；中段词组允许 Program Files 这类空格目录
_SEG = r"[A-Za-z0-9._\u4e00-\u9fff\-]+"
_SEG_MID = r"%s(?:[ ]+%s)*" % (_SEG, _SEG)
_EXT = r"\.[A-Za-z0-9]{1,8}"

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# 引号/反引号成对包裹的串（内容至少含一个分隔符才算路径，在提取时再判）
_QUOTED_RE = re.compile(r"[`'\"]([^`'\"\n\r]{2,260})[`'\"]")
# 按优先级排列的裸路径（合并为一个交替正则，finditer 天然不重叠）
# 绝对路径是强信号：中段允许空格目录，末段严格不含空格
_WIN = r"[A-Za-z]:[\\/](?:%s[\\/])*%s(?:%s)?" % (_SEG_MID, _SEG, _EXT)
_UNIX = r"(?<![A-Za-z0-9:])/(?:%s/)+%s(?:%s)?" % (_SEG_MID, _SEG, _EXT)
# 相对路径宁漏勿误报：段内不允许空格，末段带扩展名（文件）或以分隔符结束（目录）
_REL_FILE = r"(?<![\w.:\\/])%s[\\/](?:%s[\\/])*%s%s" % (_SEG, _SEG, _SEG, _EXT)
_REL_DIR = r"(?<![\w.:\\/])(?:%s[\\/])+" % _SEG
_PATH_RE = re.compile(
    "(?P<win>%s)|(?P<unix>%s)|(?P<relfile>%s)|(?P<reldir>%s)"
    % (_WIN, _UNIX, _REL_FILE, _REL_DIR)
)

_TRAILING_JUNK = ".,);]}>\"'"
_MAX_PATHS = 30


@dataclass
class PathEvidence:
    """单条被引用路径的核查证据。

    :param status: ``exists_file`` / ``exists_dir`` / ``missing`` / ``out_of_scope``
    """

    raw: str
    resolved: str = ""
    status: str = "missing"
    size: Optional[int] = None
    zero_byte: bool = False
    siblings: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "resolved": self.resolved,
            "status": self.status,
            "size": self.size,
            "zero_byte": self.zero_byte,
            "siblings": self.siblings,
            "reason": self.reason,
        }


def extract_referenced_paths(text: str, limit: int = _MAX_PATHS) -> List[str]:
    """从报错文本中提取去重后的路径候选（保持首次出现顺序）。"""
    if not text:
        return []
    masked = _URL_RE.sub(" ", text)  # URL 先挖掉，避免被当成 Unix 路径

    spans: List[tuple[int, int, str]] = []
    for m in _QUOTED_RE.finditer(masked):
        inner = m.group(1).strip()
        if any(sep in inner for sep in ("/", "\\")):
            spans.append((m.start(1), m.end(1), inner))

    quoted_spans = [(s, e) for s, e, _ in spans]
    for m in _PATH_RE.finditer(masked):
        if any(s < m.end() and e > m.start() for s, e in quoted_spans):
            continue  # 已被引号捕获，不重复取
        spans.append((m.start(), m.end(), m.group(0)))

    spans.sort(key=lambda x: x[0])

    seen: set[str] = set()
    out: List[str] = []
    for _, _, raw0 in spans:
        p = raw0.strip().strip(_TRAILING_JUNK).strip().strip("`'\"")
        if not p or len(p) > 260 or not any(sep in p for sep in ("/", "\\")):
            continue
        key = p.lower().replace("\\", "/").rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def _looks_absolute(raw: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith(("/", "\\"))


def resolve_within_root(raw: str, root: PathLike) -> Optional[Path]:
    """把路径候选锚定到游戏根，返回根内绝对路径；越界/盘符外联返回 ``None``。"""
    base = Path(root).resolve()
    p = Path(raw)
    if p.is_absolute():
        target = p.resolve()
    elif _looks_absolute(raw):
        # 非 Windows 主机上 Path 不认盘符，显式拼回
        target = Path(raw.replace("/", "\\")).resolve()
    else:
        target = (base / p).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


def _mapped(session: Any, target: Path) -> Path:
    """沙箱模式取 overlay→真实的读取映射；直接模式返回原路径。"""
    if session is not None:
        try:
            return Path(session.map_for_read(target))
        except Exception:  # noqa: BLE001  # 映射失败按真实路径兜底（只读）
            return target
    return target


def check_referenced_paths(
    text: str,
    root: PathLike,
    *,
    session: Any = None,
    limit: int = _MAX_PATHS,
) -> List[PathEvidence]:
    """提取报错文本中的路径并逐条做只读核查。"""
    refs = extract_referenced_paths(text, limit=limit)
    base = Path(root)
    if not refs:
        return []

    from ..sandbox import io as sxio  # 延迟导入，保持模块可独立测试

    evidence: List[PathEvidence] = []
    for raw in refs:
        ev = PathEvidence(raw=raw)
        if not base.is_dir():
            ev.status = "missing"
            ev.reason = "游戏目录不可访问"
            evidence.append(ev)
            continue
        target = resolve_within_root(raw, base)
        if target is None:
            ev.status = "out_of_scope"
            ev.resolved = raw
            ev.reason = "路径位于游戏目录之外，已跳过访问"
            evidence.append(ev)
            continue
        ev.resolved = str(target)
        try:
            mapped = _mapped(session, target)
            if sxio.exists(session, target):
                if sxio.is_dir(session, target):
                    ev.status = "exists_dir"
                    try:
                        ev.siblings = len(sxio.list_dir(session, target)[:64])
                    except OSError:
                        ev.siblings = None
                else:
                    ev.status = "exists_file"
                    st = mapped.stat()
                    ev.size = st.st_size
                    ev.zero_byte = st.st_size == 0
                    try:
                        ev.siblings = len(sxio.list_dir(session, target.parent)[:64])
                    except OSError:
                        ev.siblings = None
            else:
                ev.status = "missing"
                ev.reason = "报错提及的路径不存在"
        except OSError as exc:
            ev.status = "missing"
            ev.reason = f"核查失败：{exc.__class__.__name__}"
        evidence.append(ev)
    return evidence


def evidence_summary(evidence: List[PathEvidence]) -> Dict[str, List[str]]:
    """把核查结果按状态分组为原始路径（供提示词 / 报告复用）。"""
    groups: Dict[str, List[str]] = {
        "missing": [], "zero_byte": [], "exists_file": [],
        "exists_dir": [], "out_of_scope": [],
    }
    for ev in evidence:
        groups.setdefault(ev.status, []).append(ev.raw)
        if ev.status == "exists_file" and ev.zero_byte:
            groups["zero_byte"].append(ev.raw)
    return groups
