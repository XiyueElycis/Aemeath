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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
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
_DEFAULT_LOOKBACK_DAYS = 3  # 默认只查近 3 天修改过的日志；log_days<=0 表示不限日期


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

        if operation == "list":
            return self._list_logs(game_dir)
        if operation == "read":
            return self._read_log(game_dir, file_ref)
        return await self._analyze(game_dir, game_name, question)

    # ------------------------------------------------------------------ #
    def _list_logs(self, game_dir: str) -> AgentResult:
        files = self._discover_logs(game_dir)
        if not files:
            return AgentResult(
                success=True, data=LogAnalysisResult(files=[], notes=["未找到日志文件"]),
                message="未在目录中找到日志文件",
            )
        result = LogAnalysisResult(files=[str(f) for f in files])
        self._report("access", f"找到 {len(files)} 个日志文件", game_dir)
        return AgentResult(success=True, data=result, message=f"共找到 {len(files)} 个日志文件")

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

    async def _analyze(self, game_dir: str, game_name: str, question: str) -> AgentResult:
        files = self._discover_logs(game_dir)
        if not files:
            return AgentResult(
                success=True, data=LogAnalysisResult(game_name=game_name, notes=["未找到日志文件"]),
                message="未找到日志文件，无法分析",
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

        result = LogAnalysisResult(game_name=game_name, files=[str(f) for f in files], error_entries=entries)

        # 2. 可选：用大模型总结并给出建议（无密钥则跳过，回退正则报告）
        error_text = self._entries_to_text(entries)
        try:
            summary, suggestions = await self._llm_summarize(game_name, question, error_text, len(files))
            result.summary = summary
            result.suggestions = suggestions
        except LLMError:
            result.notes.append("大模型不可用，已回退为关键词提取报告")
            result.summary = self._regex_summary(entries)
            result.suggestions = self._default_suggestions(entries)

        self._report("phase", "日志分析完成", game_dir)
        return AgentResult(
            success=True,
            data=result,
            message=f"已分析 {len(files)} 个日志，提取 {len(entries)} 条报错片段",
        )

    # ------------------------------------------------------------------ #
    def _discover_logs(self, game_dir: str) -> List[Path]:
        """在 game_dir 下递归查找日志文件（深度受限、数量受限）。"""
        root = Path(game_dir) if game_dir else Path.cwd()
        if not root.is_dir():
            return []
        found: List[Path] = []
        for path in sorted(root.rglob("*")):
            if len(found) >= _MAX_FILES:
                break
            if not path.is_file():
                continue
            # 深度限制：相对根目录的层级不超过 _MAX_DEPTH
            try:
                depth = len(path.relative_to(root).parts)
            except ValueError:
                depth = 0
            if depth > _MAX_DEPTH:
                continue
            if self._is_log_file(path):
                found.append(path)
        return found

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

    def _regex_summary(self, entries: List[LogEntry]) -> str:
        if not entries:
            return "未发现明确的报错关键词，可能是非日志文件或报错在其他位置。"
        files = sorted({e.file for e in entries})
        return f"在 {len(files)} 个日志文件中发现 {len(entries)} 条疑似报错片段，涉及：{', '.join(files)}。"

    @staticmethod
    def _default_suggestions(entries: List[LogEntry]) -> List[str]:
        if not entries:
            return ["未发现明确报错，建议确认是否选对了日志目录；游戏崩溃也可能写入系统事件查看器。"]
        return [
            "优先排查上述报错行前后的上下文（如缺失 DLL、权限不足、驱动版本），"
            "可继续让我『读取』对应日志文件查看完整内容。",
            "确认游戏运行库（VC++ / DirectX / .NET）是否完整安装。",
            "如涉及显卡驱动崩溃，可尝试更新/回退 GPU 驱动。",
        ]

    # ------------------------------------------------------------------ #
    async def _llm_summarize(self, game_name: str, question: str,
                             error_text: str, file_count: int) -> tuple[str, List[str]]:
        """用大模型对报错片段做诊断并给建议，返回 (summary, suggestions)。"""
        llm = get_settings().get_llm()
        model = llm.get("model")
        client = HttpLLMClient(
            provider=llm.get("provider"),
            api_base=llm.get("api_base") or None,
            api_key=llm.get("api_key") or None,
        )
        system = (
            "你是游戏故障排查专家。根据用户日志中的报错片段，判断最可能的故障原因，"
            "输出 JSON：{\"summary\": \"一句话结论\", \"suggestions\": [\"建议1\", \"建议2\"]}。"
            "不要输出其它内容。"
        )
        user = (
            f"游戏：{game_name}\n用户问题：{question or '定位报错原因'}\n"
            f"共扫描 {file_count} 个日志文件，报错片段：\n{error_text}"
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