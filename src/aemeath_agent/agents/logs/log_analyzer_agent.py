"""游戏报错日志定位智能体。

扫描游戏目录内（或指定目录）的日志文件（``*.log`` / ``crash*`` / ``error*`` /
``output_log`` 等），提取报错、异常、崩溃片段，定位问题并给出建议。

支持 ``operation`` 分流（均为只读，安全，方案模式下也可调用）：

- ``analyze``（默认）：扫描日志文件 + 提取报错片段 + 总结 + 建议
- ``list``：列出目录下找到的日志文件
- ``read``：读取某个日志文件的尾部内容原文

大模型总结可选：配置了 LLM 密钥时才调用，否则回退为纯正则提取报告。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...analyzer.error_paths import (
    PathEvidence,
    check_referenced_paths,
    evidence_summary,
)
from ...errors import LLMError
from ...llm.client import HttpLLMClient
from ...settings import get_settings

# 日志文件名关键词（大小写不敏感，命中即视为日志）
_LOG_NAME_HINTS = (
    "log", "crash", "error", "exception", "output", "debug", "trace",
    "console", "client", "server", "unity", "unreal", "dump",
)
# 视为日志的扩展名
_LOG_SUFFIXES = (".log", ".dmp", ".trace", ".trc", ".txt")

# 报错关键词（正则初筛，中英混杂）
_ERROR_RE = re.compile(
    r"(error|exception|fatal|critical|failed|failure|traceback|crash|"
    r"not found|denied|access violation|segfault|cannot|unable|timeout|"
    r"错误|异常|失败|崩溃|拒绝|无法|缺失|超时)",
    re.IGNORECASE,
)

_MAX_FILES = 20        # 最多扫描的日志文件数（按修改时间倒序取最新）
_MAX_READ_CHARS = 30000  # 每个文件最多读取的字符（取尾部，避免超大文件）
_MAX_DEPTH = 4         # 目录遍历深度
_MAX_ERROR_LINES = 60  # 最多保留的报错行数
_MAX_KB_QUERIES = 8    # 知识库签名检索最多用多少条报错行
_MAX_KB_HITS = 5       # 最多保留多少条知识命中
_MAX_EVIDENCE_REPORTS = 10  # 引用路径访问事件最多上报多少个目录
_TEMPLATE_CUT = 500    # 修复模板截断长度
_DEFAULT_LOOKBACK_DAYS = 3  # 默认只查近 3 天修改过的日志；log_days<=0 表示不限日期

# 报错行中的异常/错误类型名（Java/C++/运行库常见后缀），用于知识库短签名检索。
# 库存签名是「UnsatisfiedLinkError」这类词，整行报错去做 LIKE 反而匹配不到。
_SIG_TYPE_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Violation|Fault|Abort|Crash)\b"
)


@dataclass
class LogEntry:
    """单条报错片段。"""

    file: str
    line: int
    text: str


@dataclass
class LogAnalysisResult:
    """日志分析聚合结果。"""

    game_name: str = ""
    files: List[str] = field(default_factory=list)
    error_entries: List[LogEntry] = field(default_factory=list)
    # 报错文本中被引用路径的针对性核查结果（存在/缺失/0 字节/越界）
    referenced_paths: List[Dict[str, Any]] = field(default_factory=list)
    # 本地知识库按报错签名命中的修复方案
    knowledge_hits: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    suggestions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class LogAnalyzerAgent(BaseAgent):
    """报错日志定位智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("log_analyzer", config)
        self.capabilities = ["log_analysis", "error_detection", "crash_analysis", "file_read"]

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Log Analyzer Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        data = task.data or {}
        operation = (data.get("operation") or "analyze").lower()
        game_dir = data.get("game_dir") or ""
        file_ref = data.get("file") or ""
        game_name = data.get("game_name") or (Path(game_dir).name if game_dir else "Unknown")
        question = data.get("user_message") or data.get("question") or ""
        # 只扫描近 N 天修改过的日志；缺省近 3 天，log_days<=0 表示不限日期
        try:
            lookback_days = int(data.get("log_days", _DEFAULT_LOOKBACK_DAYS))
        except (TypeError, ValueError):
            lookback_days = _DEFAULT_LOOKBACK_DAYS

        if operation == "list":
            return self._list_logs(game_dir, lookback_days)
        if operation == "read":
            return self._read_log(game_dir, file_ref)
        return await self._analyze(
            game_dir, game_name, question, lookback_days,
            session=self._sandbox(data),
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _lookback_note(lookback_days: int) -> str:
        return "不限日期" if lookback_days <= 0 else f"近 {lookback_days} 天"

    def _list_logs(self, game_dir: str, lookback_days: int = _DEFAULT_LOOKBACK_DAYS) -> AgentResult:
        files = self._discover_logs(game_dir, lookback_days)
        scope = self._lookback_note(lookback_days)
        if not files:
            return AgentResult(
                success=True,
                data=LogAnalysisResult(files=[], notes=[f"{scope}内修改过的日志文件未找到"]),
                message=f"{scope}内修改过的日志文件未找到",
            )
        result = LogAnalysisResult(files=[str(f) for f in files], notes=[f"扫描范围：{scope}修改过的日志"])
        self._report("access", f"找到 {len(files)} 个日志文件（{scope}）", game_dir)
        return AgentResult(
            success=True, data=result,
            message=f"{scope}内共找到 {len(files)} 个日志文件",
        )

    def _read_log(self, game_dir: str, file_ref: str) -> AgentResult:
        target = self._resolve_path(game_dir, file_ref)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        if not target.is_file():
            return AgentResult(success=False, message=f"日志文件不存在：{target}", errors=[str(target)])
        self._report("access", f"正在读取日志：{target}", str(target))
        content = self._read_tail(target)
        return AgentResult(
            success=True,
            data={"file": str(target), "content": content},
            message=f"已读取 {target.name}（尾部 {len(content)} 字符）",
        )

    async def _analyze(self, game_dir: str, game_name: str, question: str,
                       lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
                       *, session: Any = None) -> AgentResult:
        files = self._discover_logs(game_dir, lookback_days)
        scope = self._lookback_note(lookback_days)
        if not files:
            return AgentResult(
                success=True,
                data=LogAnalysisResult(game_name=game_name, notes=[f"{scope}内修改过的日志文件未找到"]),
                message=f"{scope}内修改过的日志文件未找到，无法分析",
            )

        # 1. 提取报错片段（纯本地，不依赖 LLM）
        entries: List[LogEntry] = []
        for f in files:
            self._report("access", f"解析日志：{f.name}", str(f))
            for e in self._extract_errors(f):
                entries.append(e)
                if len(entries) >= _MAX_ERROR_LINES:
                    break
            if len(entries) >= _MAX_ERROR_LINES:
                break

        result = LogAnalysisResult(
            game_name=game_name, files=[str(f) for f in files], error_entries=entries,
            notes=[f"扫描范围：{scope}修改过的日志"],
        )
        error_text = self._entries_to_text(entries)

        # 2. 证据闭环：顺着报错文本里出现的路径做针对性只读核查
        evidence: List[PathEvidence] = []
        groups: Dict[str, List[str]] = {}
        if game_dir and entries:
            evidence = check_referenced_paths(error_text, game_dir, session=session)
            result.referenced_paths = [ev.to_dict() for ev in evidence]
            groups = evidence_summary(evidence)
            self._report_evidence_dirs(evidence)
            for raw in groups.get("out_of_scope", []):
                result.notes.append(f"报错引用了游戏目录外路径，已拒绝访问：{raw}")

        # 3. 证据闭环：按报错签名检索本地知识库（失败不阻断分析）
        result.knowledge_hits = self._knowledge_lookup(entries)
        if not result.knowledge_hits:
            result.notes.append("本地知识库未命中该报错签名")

        evidence_text = self._evidence_to_text(groups, result.knowledge_hits)

        # 4. 可选：用大模型总结并给出建议（无密钥则跳过，回退正则报告）
        try:
            summary, suggestions = await self._llm_summarize(
                game_name, question, error_text, len(files), evidence_text,
            )
            result.summary = summary
            result.suggestions = suggestions
        except LLMError:
            result.notes.append("大模型不可用，已回退为关键词提取报告")
            result.summary = self._regex_summary(entries, groups)
            result.suggestions = self._default_suggestions(entries, groups)

        self._report("phase", f"日志分析完成（{scope}）", game_dir)
        missing_n = len(groups.get("missing", []))
        zero_n = len(groups.get("zero_byte", []))
        return AgentResult(
            success=True,
            data=result,
            message=(
                f"{scope}内已分析 {len(files)} 个日志，提取 {len(entries)} 条报错片段；"
                f"核查报错引用路径 {len(evidence)} 个"
                + (f"（缺失 {missing_n}" + (f"、0 字节 {zero_n}" if zero_n else "") + "）"
                   if evidence else "")
                + f"，知识库命中 {len(result.knowledge_hits)} 条"
            ),
        )

    def _report_evidence_dirs(self, evidence: List[PathEvidence]) -> None:
        """目录级节流上报：同目录多个路径合并为一条，避免事件流被刷爆。"""
        buckets: Dict[str, int] = {}
        order: List[str] = []
        for ev in evidence:
            if ev.status == "out_of_scope":
                continue
            parent = str(Path(ev.raw).parent).replace("\\", "/").strip("/.")
            if not parent:
                parent = "<游戏根目录>"
            key = parent.lower()
            if key not in buckets:
                buckets[key] = 0
                order.append(key)
            buckets[key] += 1
        for i, key in enumerate(order[:_MAX_EVIDENCE_REPORTS]):
            n = buckets[key]
            self._report(
                "access",
                f"核查报错提及位置：{key}（{n} 个路径）",
                key,
            )
        if len(order) > _MAX_EVIDENCE_REPORTS:
            self._report(
                "access",
                f"另有 {len(order) - _MAX_EVIDENCE_REPORTS} 个报错提及位置已核查",
                "",
            )

    @staticmethod
    def _signatures_from_line(line: str) -> List[str]:
        """从一条报错行派生知识库查询签名：异常类型名 + 短行原文。

        例：``Caused by: java.lang.UnsatisfiedLinkError: ...`` →
        ``["UnsatisfiedLinkError"]``；短报错行额外保留整行（覆盖中文签名）。
        """
        sigs: List[str] = []
        for m in _SIG_TYPE_RE.finditer(line):
            name = m.group(0).split(".")[-1]  # 去掉包名前缀
            if len(name) >= 5 and name not in sigs:
                sigs.append(name)
        stripped = line.strip()
        if 2 <= len(stripped) <= 60 and stripped not in sigs:
            sigs.append(stripped)
        return sigs[:2]

    @staticmethod
    def _knowledge_lookup(entries: List[LogEntry]) -> List[Dict[str, Any]]:
        """对去重后的报错签名检索本地 SQLite 知识库，任何异常都不阻断。"""
        if not entries:
            return []
        try:
            from ...knowledge.retriever import SQLiteRetriever
            retriever = SQLiteRetriever()
        except Exception:  # noqa: BLE001
            return []
        seen_sig: set[str] = set()
        hits_by_key: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            for sig in LogAnalyzerAgent._signatures_from_line(entry.text):
                if len(seen_sig) >= _MAX_KB_QUERIES:
                    break
                key0 = sig.lower()
                if not sig or key0 in seen_sig:
                    continue
                seen_sig.add(key0)
                try:
                    for hit in retriever.search(sig):
                        if hit.key in hits_by_key:
                            continue
                        template = (hit.repair_template or "")[:_TEMPLATE_CUT]
                        hits_by_key[hit.key] = {
                            "key": hit.key,
                            "score": round(float(getattr(hit, "score", 0.0)), 3),
                            "repair_template": template,
                        }
                except Exception:  # noqa: BLE001
                    continue
            if len(hits_by_key) >= _MAX_KB_HITS or len(seen_sig) >= _MAX_KB_QUERIES:
                break
        return list(hits_by_key.values())[:_MAX_KB_HITS]

    @staticmethod
    def _evidence_to_text(groups: Dict[str, List[str]],
                          knowledge_hits: List[Dict[str, Any]]) -> str:
        """把路径核查与知识命中拼成提示词补充上下文。"""
        lines: List[str] = []
        if groups:
            lines.append("路径核查（顺着报错文本反查游戏目录，只读）：")
            labels = [
                ("missing", "不存在"), ("zero_byte", "存在但为 0 字节"),
                ("exists_file", "存在文件"), ("exists_dir", "存在目录"),
            ]
            for key, label in labels:
                vals = groups.get(key) or []
                if vals:
                    lines.append(f"- {label}：{', '.join(vals[:8])}")
        if knowledge_hits:
            lines.append("本地知识库命中的修复方案（参考，勿原样复述）：")
            for i, hit in enumerate(knowledge_hits[:3], 1):
                lines.append(f"{i}. [{hit['key']}] {hit['repair_template'][:200]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def _discover_logs(self, game_dir: str,
                       lookback_days: int = _DEFAULT_LOOKBACK_DAYS) -> List[Path]:
        """在 game_dir 下递归查找日志文件。

        - 深度受限（_MAX_DEPTH）、数量受限（_MAX_FILES）；
        - lookback_days>0 时只保留最近 N 天修改过的文件；<=0 表示不限日期；
        - 先收集全部候选，再按修改时间倒序取最新的 _MAX_FILES 个。
        """
        root = Path(game_dir) if game_dir else Path.cwd()
        if not root.is_dir():
            return []
        cutoff = time.time() - lookback_days * 86400 if lookback_days > 0 else 0.0
        candidates: List[tuple[float, Path]] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            # 深度限制：相对根目录的层级不超过 _MAX_DEPTH
            try:
                depth = len(path.relative_to(root).parts)
            except ValueError:
                depth = 0
            if depth > _MAX_DEPTH or not self._is_log_file(path):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if lookback_days > 0 and mtime < cutoff:
                continue
            candidates.append((mtime, path))
        # 按修改时间倒序（最新优先），同时间再按文件名保证稳定顺序
        candidates.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [p for _, p in candidates[:_MAX_FILES]]

    @staticmethod
    def _is_log_file(path: Path) -> bool:
        name = path.name.lower()
        if path.suffix.lower() in _LOG_SUFFIXES and any(k in name for k in _LOG_NAME_HINTS):
            return True
        return path.suffix.lower() in (".log", ".dmp", ".trace", ".trc")

    def _extract_errors(self, path: Path) -> List[LogEntry]:
        """逐行提取含报错关键词的行。"""
        text = self._read_tail(path)
        entries: List[LogEntry] = []
        for i, line in enumerate(text.splitlines()):
            if _ERROR_RE.search(line):
                entries.append(LogEntry(file=path.name, line=i + 1, text=line.strip()[:300]))
        return entries

    @staticmethod
    def _read_tail(path: Path) -> str:
        """读取文件尾部（最多 _MAX_READ_CHARS 字符），避免超大文件拖垮内存。"""
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
        if len(raw) > _MAX_READ_CHARS * 4:
            raw = raw[-_MAX_READ_CHARS * 4:]
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")

    def _resolve_path(self, game_dir: str, file_ref: str) -> Path | str:
        """把文件引用解析为绝对路径。"""
        if not file_ref:
            return "未提供日志文件路径"
        p = Path(file_ref)
        if p.is_absolute():
            return p
        root = Path(game_dir).resolve() if game_dir else Path.cwd()
        target = (root / p).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return "目标文件不在游戏目录内，已拒绝"
        return target

    @staticmethod
    def _entries_to_text(entries: List[LogEntry]) -> str:
        if not entries:
            return "（未发现明确报错行）"
        return "\n".join(f"[{e.file}:{e.line}] {e.text}" for e in entries[:40])

    def _regex_summary(self, entries: List[LogEntry],
                       groups: Dict[str, List[str]] | None = None) -> str:
        if not entries:
            return "未发现明确的报错关键词，可能是非日志文件或报错在其他位置。"
        files = sorted({e.file for e in entries})
        text = f"在 {len(files)} 个日志文件中发现 {len(entries)} 条疑似报错片段，涉及：{', '.join(files)}。"
        groups = groups or {}
        missing = groups.get("missing") or []
        zero = groups.get("zero_byte") or []
        if missing:
            text += f" 报错引用但实际不存在的路径：{', '.join(missing[:5])}。"
        if zero:
            text += f" 0 字节文件（疑似下载中断/被杀软隔离）：{', '.join(zero[:5])}。"
        return text

    @staticmethod
    def _default_suggestions(entries: List[LogEntry],
                             groups: Dict[str, List[str]] | None = None) -> List[str]:
        groups = groups or {}
        suggestions: List[str] = []
        missing = groups.get("missing") or []
        zero = groups.get("zero_byte") or []
        if missing:
            suggestions.append(
                "报错引用的以下路径不存在，优先核实文件是否被误删/被隔离/路径大小写："
                + "、".join(missing[:5])
            )
        if zero:
            suggestions.append(
                "以下文件为 0 字节，通常是下载中断或杀软隔离，建议校验游戏完整性后重试："
                + "、".join(zero[:5])
            )
        if not entries:
            return ["未发现明确报错，建议确认是否选对了日志目录；游戏崩溃也可能写入系统事件查看器。"]
        suggestions.extend([
            "优先排查上述报错行前后的上下文（如缺失 DLL、权限不足、驱动版本），"
            "可继续让我『读取』对应日志文件查看完整内容。",
            "确认游戏运行库（VC++ / DirectX / .NET）是否完整安装。",
            "如涉及显卡驱动崩溃，可尝试更新/回退 GPU 驱动。",
        ])
        return suggestions

    # ------------------------------------------------------------------ #
    async def _llm_summarize(self, game_name: str, question: str,
                             error_text: str, file_count: int,
                             evidence_text: str = "") -> tuple[str, List[str]]:
        """用大模型对报错片段做诊断并给建议，返回 (summary, suggestions)。"""
        llm = get_settings().get_llm()
        model = llm.get("model")
        client = HttpLLMClient(
            provider=llm.get("provider"),
            api_base=llm.get("api_base") or None,
            api_key=llm.get("api_key") or None,
        )
        system = (
            "你是游戏故障排查专家。根据用户日志中的报错片段与系统已完成的只读核查证据，"
            "判断最可能的故障原因，"
            "输出 JSON：{\"summary\": \"一句话结论\", \"suggestions\": [\"建议1\", \"建议2\"]}。"
            "核查证据中的路径存在性是本机实测结果，应优先采信；不要输出其它内容。"
        )
        evidence_block = f"\n核查证据：\n{evidence_text}\n" if evidence_text else ""
        user = (
            f"游戏：{game_name}\n用户问题：{question or '定位报错原因'}\n"
            f"共扫描 {file_count} 个日志文件，报错片段：\n{error_text}"
            f"{evidence_block}"
        )
        raw = await asyncio.to_thread(
            client.complete_messages,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model,
            temperature=0.2,
            max_tokens=800,
        )
        try:
            import json
            obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            summary = str(obj.get("summary") or "").strip()
            suggestions = [str(s) for s in obj.get("suggestions", [])]
        except (ValueError, KeyError):
            summary = raw.strip()
            suggestions = []
        return summary or raw.strip(), suggestions