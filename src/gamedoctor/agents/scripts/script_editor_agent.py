"""游戏脚本 / 配置文件编辑智能体（完整 CRUD + 替换）。

让大模型对游戏目录下的脚本与配置文件做**增、删、查、改、替换**。不同游戏使用的
脚本语言不同（Lua / Python / C# / JSON / INI / XML 等），本智能体：

- 按**扩展名**识别语言并据此提示大模型用对应语法改写；
- 用**白名单**限制可编辑的文件类型（:data:`LANGUAGE_MAP`），拒绝未知扩展名；
- **任何破坏性操作（改 / 增 / 删 / 替换）执行前都先备份**原文件到
  ``~/.gamedoctor/backups``；
- 支持 ``operation`` 分流：``edit``（默认，改）/ ``create``（增）/ ``delete``（删）
  / ``read``（查内容）/ ``list``（查目录）/ ``replace``（用另一文件替换）;
- 全程通过 :func:`BaseAgent._report` 上报 ``access`` / ``modify`` 事件，
  供 GUI 「操作过程可视化」展示。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...errors import LLMError
from ...llm.client import HttpLLMClient
from ...sandbox import SandboxViolation
from ...sandbox import io as sxio
from ...settings import get_settings

# 扩展名（小写、无点）→ 语言名。既是可编辑类型白名单，也用于 prompt 提示
# 与能力描述。新增语言只需在这里加一行。
LANGUAGE_MAP: Dict[str, str] = {
    "lua": "Lua",
    "py": "Python",
    "js": "JavaScript",
    "ts": "TypeScript",
    "json": "JSON",
    "ini": "INI",
    "cfg": "CFG 配置",
    "conf": "配置文件",
    "xml": "XML",
    "toml": "TOML",
    "yaml": "YAML",
    "yml": "YAML",
    "cs": "C#",
    "c": "C",
    "cpp": "C++",
    "h": "C/C++ 头文件",
    "java": "Java",
    "kt": "Kotlin",
    "sh": "Shell",
    "bat": "Batch",
    "ps1": "PowerShell",
    "sql": "SQL",
    "txt": "纯文本",
}

def _strip_code_fence(text: str) -> str:
    """剥离大模型可能包裹的 ```lang ... ``` 围栏，返回纯净代码。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).rstrip() + "\n"
    return text


@dataclass
class ScriptEditResult:
    """脚本编辑结果。"""

    file: str
    operation: str = "edit"
    language: str = ""
    backup_path: str = ""
    original_length: int = 0
    new_length: int = 0
    changed: bool = False
    listing: List[Dict[str, Any]] = field(default_factory=list)
    content: str = ""
    notes: List[str] = field(default_factory=list)


class ScriptEditorAgent(BaseAgent):
    """脚本 / 配置文件编辑智能体（增删改查 + 替换）。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("script_editor", config)
        self.capabilities = [
            "script_edit", "file_read", "file_write", "file_create", "file_delete",
            "file_replace", "file_list", "code_edit", "multi_language_edit",
        ]
        # 允许在无 game_dir 时编辑任意绝对路径文件（否则只限 game_dir 内）
        self.allow_absolute = bool(self.config.get("allow_absolute", True))

    def _do_initialize(self) -> None:
        supported = "、".join(sorted(LANGUAGE_MAP.values()))
        self.logger.info("Initializing Script Editor Agent（支持 %s）", supported)

    async def execute(self, task: AgentTask) -> AgentResult:
        """按 operation 分流到具体 CRUD 实现。"""
        data = task.data or {}
        operation = (data.get("operation") or "edit").lower()
        game_dir = data.get("game_dir") or ""
        file_ref = data.get("file") or ""
        instruction = data.get("user_message") or data.get("question") or ""
        sx = self._sandbox(data)

        try:
            if operation == "list":
                result = self._do_list(sx, game_dir, file_ref)
            elif operation == "delete":
                result = self._do_delete(sx, game_dir, file_ref)
            elif operation == "read":
                result = self._do_read(sx, game_dir, file_ref)
            elif operation == "replace":
                result = self._do_replace(sx, game_dir, file_ref,
                                          data.get("source") or "")
            elif operation == "create":
                result = await self._do_create(sx, game_dir, file_ref, instruction)
            else:
                # 默认 edit
                result = await self._do_edit(sx, game_dir, file_ref, instruction)
        except SandboxViolation as exc:
            return AgentResult(success=False, message=str(exc), errors=[str(exc)])

        # 沙箱模式：写操作只改了副本，必须如实提示尚未落盘
        if sx is not None and result.success and operation not in ("read", "list"):
            result.message += "（已在沙箱中完成，待你审核应用后才会真正生效）"
        return result

    # ------------------------------------------------------------------ #
    async def _do_edit(self, sx, game_dir: str, file_ref: str, instruction: str) -> AgentResult:
        """改：读原文件 → LLM 生成新内容 → 备份 → 写入（沙箱模式全部重定向）。"""
        target = self._resolve_file(game_dir, file_ref)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        if not sxio.exists(sx, target):
            return AgentResult(success=False, message=f"目标文件不存在：{target}", errors=[f"文件不存在：{target}"])
        if sxio.is_dir(sx, target):
            return AgentResult(
                success=False,
                message=f"目标是目录而非文件，脚本编辑仅支持文件：{target}",
                errors=[f"目标是目录：{target}"])
        lang = self._lang_of(target)
        if lang is None:
            return AgentResult(success=False, message=self._unsupported_msg(target))

        self._report("access", f"正在读取文件：{target}", str(target))
        original = sxio.read_text(sx, target)

        self._report("phase", f"正在让大模型编辑 {lang} 脚本...", str(target))
        try:
            new_content = await self._generate_content(target, lang, original, instruction)
        except LLMError as exc:
            return AgentResult(success=False, message=f"大模型调用失败：{exc}", errors=[str(exc)])

        result = ScriptEditResult(
            file=str(target), operation="edit", language=lang,
            original_length=len(original), new_length=len(new_content),
            changed=new_content.rstrip() != original.rstrip(),
        )
        if not result.changed:
            result.notes.append("内容与原文一致，未写入")
            return AgentResult(success=True, data=result, message="内容无变化，已跳过写入")

        try:
            result.backup_path = str(self._backup(sx, target))
        except OSError as exc:
            return AgentResult(success=False, message=f"备份失败，已中止写入：{exc}", errors=[str(exc)])
        self._report("modify", f"正在修改文件：{target}", str(target))
        try:
            sxio.write_text(sx, target, new_content)
        except OSError as exc:
            return AgentResult(success=False, message=f"写入失败：{exc}（原文件已备份）", errors=[str(exc)])

        self._report("phase", f"已修改 {target.name}", str(target))
        return AgentResult(success=True, data=result, message=f"已编辑 {lang} 文件 {target.name}")

    async def _do_create(self, sx, game_dir: str, file_ref: str, instruction: str) -> AgentResult:
        """增：由 LLM 生成新文件内容 → 备份（若已存在） → 写入新文件。"""
        target = self._resolve_file(game_dir, file_ref, must_exist=False)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        lang = self._lang_of(target)
        if lang is None:
            return AgentResult(success=False, message=self._unsupported_msg(target))
        if sxio.is_dir(sx, target):
            return AgentResult(
                success=False,
                message=f"目标路径已被同名目录占用，无法按文件创建：{target}",
                errors=[f"目标是目录：{target}"])

        self._report("phase", f"正在让大模型生成 {lang} 文件内容...", str(target))
        original = sxio.read_text(sx, target) if sxio.exists(sx, target) else ""
        try:
            new_content = await self._generate_content(target, lang, original, instruction or f"创建 {target.name}")
        except LLMError as exc:
            return AgentResult(success=False, message=f"大模型调用失败：{exc}", errors=[str(exc)])

        result = ScriptEditResult(file=str(target), operation="create", language=lang, new_length=len(new_content))
        if sxio.exists(sx, target):
            try:
                result.backup_path = str(self._backup(sx, target))
            except OSError as exc:
                return AgentResult(success=False, message=f"备份失败，已中止创建：{exc}", errors=[str(exc)])
        self._report("modify", f"正在创建文件：{target}", str(target))
        try:
            sxio.write_text(sx, target, new_content)
        except OSError as exc:
            return AgentResult(success=False, message=f"创建失败：{exc}", errors=[str(exc)])

        self._report("phase", f"已创建 {target.name}", str(target))
        return AgentResult(success=True, data=result, message=f"已创建 {lang} 文件 {target.name}")

    def _do_delete(self, sx, game_dir: str, file_ref: str) -> AgentResult:
        """删：备份后删除文件（沙箱模式只记 tombstone，真实文件不动）。"""
        target = self._resolve_file(game_dir, file_ref)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        if not sxio.exists(sx, target):
            return AgentResult(success=False, message=f"目标文件不存在：{target}", errors=[f"文件不存在：{target}"])
        if sxio.is_dir(sx, target):
            return AgentResult(
                success=False,
                message=f"目标是目录，脚本编辑仅支持删除文件，不支持删除目录：{target}",
                errors=[f"目标是目录：{target}"])

        self._report("access", f"正在删除文件：{target}", str(target))
        try:
            backup = self._backup(sx, target)
        except OSError as exc:
            return AgentResult(success=False, message=f"备份失败，已中止删除：{exc}", errors=[str(exc)])
        self._report("modify", f"正在删除文件：{target}", str(target))
        try:
            sxio.remove(sx, target)
        except OSError as exc:
            return AgentResult(success=False, message=f"删除失败：{exc}", errors=[str(exc)])

        result = ScriptEditResult(file=str(target), operation="delete", backup_path=str(backup))
        self._report("phase", f"已删除 {target.name}", str(target))
        return AgentResult(success=True, data=result, message=f"已删除文件 {target.name}")

    def _do_read(self, sx, game_dir: str, file_ref: str) -> AgentResult:
        """查：读取文件内容原文。"""
        target = self._resolve_file(game_dir, file_ref)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        if not sxio.exists(sx, target):
            return AgentResult(success=False, message=f"目标文件不存在：{target}", errors=[f"文件不存在：{target}"])
        self._report("access", f"正在读取文件：{target}", str(target))
        content = sxio.read_text(sx, target)
        result = ScriptEditResult(file=str(target), operation="read", language=self._lang_of(target) or "", content=content)
        return AgentResult(success=True, data=result, message=f"已读取 {target.name}")

    def _do_replace(self, sx, game_dir: str, file_ref: str, source_ref: str) -> AgentResult:
        """替换：用 source 文件的内容覆盖 target 文件，target 先备份。"""
        if not source_ref:
            return AgentResult(success=False, message="替换操作需提供 source（源文件路径）", errors=["source 为空"])
        target = self._resolve_file(game_dir, file_ref)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        if not sxio.exists(sx, target):
            return AgentResult(success=False, message=f"目标文件不存在：{target}", errors=[f"文件不存在：{target}"])
        if sxio.is_dir(sx, target):
            return AgentResult(
                success=False,
                message=f"目标是目录而非文件，无法替换：{target}",
                errors=[f"目标是目录：{target}"])
        source = self._resolve_file(game_dir, source_ref, must_exist=False)
        if isinstance(source, str):
            return AgentResult(success=False, message=source, errors=[source])
        if not sxio.exists(sx, source):
            return AgentResult(success=False, message=f"源文件不存在：{source}", errors=[f"源文件不存在：{source}"])
        if sxio.is_dir(sx, source):
            return AgentResult(
                success=False,
                message=f"源是目录而非文件，replace 仅支持文件间替换：{source}",
                errors=[f"源是目录：{source}"])

        self._report("access", f"正在用 {source.name} 替换 {target.name}", str(target))
        try:
            backup = self._backup(sx, target)
        except OSError as exc:
            return AgentResult(success=False, message=f"备份失败，已中止替换：{exc}", errors=[str(exc)])
        self._report("modify", f"正在替换文件：{target}", str(target))
        try:
            sxio.replace_file(sx, target, source)
        except OSError as exc:
            return AgentResult(success=False, message=f"替换失败：{exc}", errors=[str(exc)])

        result = ScriptEditResult(
            file=str(target), operation="replace",
            language=self._lang_of(target) or "", backup_path=str(backup),
            notes=[f"源文件：{source}"],
        )
        self._report("phase", f"已替换 {target.name}", str(target))
        return AgentResult(success=True, data=result, message=f"已将 {target.name} 替换为 {source.name}")

    def _do_list(self, sx, game_dir: str, file_ref: str) -> AgentResult:
        """查目录：列出目录下的文件，供大模型/用户选择操作对象。"""
        target = self._resolve_dir(game_dir, file_ref)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        self._report("access", f"正在列出目录：{target}", str(target))
        try:
            entries = sxio.list_dir(sx, target)
        except OSError as exc:
            return AgentResult(success=False, message=f"列目录失败：{exc}", errors=[str(exc)])
        listing = [
            {"name": e["name"], "path": e["path"],
             "type": "dir" if e["is_dir"] else "file", "size": e["size"]}
            for e in entries
        ]
        result = ScriptEditResult(file=str(target), operation="list", listing=listing)
        return AgentResult(success=True, data=result, message=f"目录 {target} 共 {len(listing)} 项")

    # ------------------------------------------------------------------ #
    def _supported_ext(self) -> str:
        return "、".join(f".{e}" for e in sorted(LANGUAGE_MAP))

    def _lang_of(self, target: Path) -> str | None:
        return LANGUAGE_MAP.get(target.suffix.lstrip(".").lower())

    def _unsupported_msg(self, target: Path) -> str:
        return f"暂不支持编辑 .{target.suffix.lstrip('.').lower()} 文件；支持：{self._supported_ext()}"

    def _resolve_dir(self, game_dir: str, file_ref: str) -> Path | str:
        """解析目录：优先 file_ref，否则 game_dir。"""
        p = Path(file_ref) if file_ref else (Path(game_dir) if game_dir else Path.cwd())
        if p.is_absolute():
            if not self.allow_absolute and game_dir and not str(p).startswith(str(Path(game_dir).resolve())):
                return "已禁用访问游戏目录外的绝对路径"
            p = p if p.is_dir() else p.parent
            return p if p.is_dir() else "目录不存在"
        root = Path(game_dir).resolve() if game_dir else Path.cwd()
        return root if root.is_dir() else "游戏根目录不存在"

    def _resolve_file(self, game_dir: str, file_ref: str, must_exist: bool = True) -> Path | str:
        """把用户/大模型给的文件引用解析为绝对 Path；非法返回错误说明字符串。

        :param must_exist: False 时允许目标文件尚不存在（create 场景）。
        """
        if not file_ref:
            return "未提供要编辑的文件路径，请在需求里指明脚本文件"
        p = Path(file_ref)
        if p.is_absolute():
            if not self.allow_absolute:
                return "已禁用编辑游戏目录外的绝对路径文件"
            return p
        if not game_dir:
            return "未设置游戏根目录，无法定位相对路径文件，请填写游戏根目录或给绝对路径"
        root = Path(game_dir).resolve()
        target = (root / p).resolve()
        # 防路径穿越：解析后必须仍在游戏根目录内
        try:
            target.relative_to(root)
        except ValueError:
            return "目标文件不在游戏根目录内，已拒绝（可能的路径穿越）"
        return target

    def _backup(self, sx, target: Path) -> Path:
        """把原文件复制到 ``~/.gamedoctor/backups/<文件名>/<时间戳>/``。

        沙箱模式下备份位置位于授权根之外，作为辅助输出重定向到沙箱 side 区
        （审核应用时才写回真实备份位置）；源始终取真实磁盘原件而非沙箱修改稿。
        """
        backup_dir = (
            Path.home()
            / ".gamedoctor"
            / "backups"
            / target.stem
            / datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        sxio.ensure_dir(sx, backup_dir, side=True)
        dest = backup_dir / target.name
        sxio.copy_file(sx, target, dest, side_dst=True, pristine_src=True)
        return dest

    # ------------------------------------------------------------------ #
    def _build_llm_client(self) -> HttpLLMClient:
        """按 GUI 运行时设置构造 LLM 客户端（与对话服务共用同一套配置）。"""
        llm = get_settings().get_llm()
        return HttpLLMClient(
            provider=llm.get("provider"),
            api_base=llm.get("api_base") or None,
            api_key=llm.get("api_key") or None,
        )

    async def _generate_content(
        self, target: Path, lang: str, original: str, instruction: str
    ) -> str:
        """让大模型按指令生成文件内容（编辑/创建共用），返回完整文件新内容。"""
        llm = get_settings().get_llm()
        model = llm.get("model")
        client = self._build_llm_client()
        system = (
            "你是资深游戏脚本/配置修改助手。请严格按照用户的修改要求改写脚本，"
            "直接输出修改后的【完整文件内容】。不要输出任何解释、不要用 markdown "
            "代码块围栏包裹。"
        )
        user = (
            f"文件路径：{target}\n"
            f"语言：{lang}\n\n"
            f"当前文件内容：\n{original}\n\n"
            f"修改要求：{instruction}\n\n"
            f"请输出修改后的完整文件内容。"
        )
        raw = await asyncio.to_thread(
            client.complete_messages,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model,
        )
        return _strip_code_fence(raw)