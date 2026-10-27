"""编程智能体 · 工具层。

工具用「名字 + JSON 参数」描述，由模型在 JSON 契约里声明要调哪个；执行结果
作为观察文本回灌给模型。刻意不用 provider 原生 function calling：本项目对接
多家 OpenAI 兼容端点，原生 tool_use 支持参差不齐，而 JSON 契约在各家都稳。

写文件与执行命令一律先过审批回调（:class:`ApprovalAsk`）——这是本模块的
安全闸门，等价于 Claude Code 的「改之前先让你看一眼」。
"""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import git as git_tools
from .repomap import build_repo_map
from .workspace import Workspace, WorkspaceError

# 命令输出上限：再长模型也读不完，只会挤占上下文
MAX_OUTPUT_CHARS = 8_000
# 命令默认超时（秒）
DEFAULT_TIMEOUT = 120
# 只读工具：不需要审批
READONLY_TOOLS = frozenset({
    "list_files", "read_file", "grep", "glob_files",
    "repo_map", "git_status", "git_diff", "git_log", "git_branch",
    "list_checkpoints", "search_code", "mcp_list",
})
# 写工具 / 执行工具：需要审批
WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "git_commit",
    "make_dir", "move_path", "copy_path", "delete_path", "undo_change",
    "git_checkout", "git_push", "git_pr", "mcp_call",
})
SHELL_TOOLS = frozenset({"run_command"})

# 工具说明书：既用于拼 system prompt，也用于参数校验
TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "list_files": {
        "description": "列出目录内容（默认当前目录，最多 4 层）。",
        "args": {"path": "目录路径，默认 '.'", "depth": "层级，默认 1，最大 4"},
    },
    "read_file": {
        "description": "读文本文件；可选择行区间，返回带行号内容。",
        "args": {"path": "文件路径", "start": "起始行（1 起，可省略）",
                 "end": "结束行（含，可省略）"},
    },
    "grep": {
        "description": "用正则搜索文件内容，返回 文件:行号: 内容。",
        "args": {"pattern": "正则表达式", "path": "搜索范围，默认 '.'",
                 "glob": "只搜匹配的文件名，如 '*.py'，可省略",
                 "limit": "最多返回条数，默认 60"},
    },
    "glob_files": {
        "description": "按文件名模式查找文件，支持 '**' 与 '*.py'。",
        "args": {"pattern": "文件名模式", "limit": "最多返回条数，默认 100"},
    },
    "write_file": {
        "description": "整文件写入（覆盖已有内容）。需要审批。",
        "args": {"path": "文件路径", "content": "完整新内容"},
    },
    "edit_file": {
        "description": "把文件里唯一命中的一段原文替换成新文本。需要审批。",
        "args": {"path": "文件路径", "old_text": "要被替换的原文（必须唯一命中）",
                 "new_text": "替换后的文本"},
    },
    "run_command": {
        "description": "在工作区根目录执行一条 shell 命令并返回输出。需要审批。",
        "args": {"command": "要执行的命令", "timeout": "超时秒数，默认 120"},
    },
    "repo_map": {
        "description": "生成仓库地图：文件名 + 行数 + 关键符号所在行。"
                       "第一次接触陌生目录时先用它，比逐个 list_files 高效。",
        "args": {"path": "范围，默认 '.'", "max_files": "最多文件数，默认 300"},
    },
    "git_status": {
        "description": "查看当前分支与未提交改动。",
        "args": {},
    },
    "git_diff": {
        "description": "查看工作区差异（默认未暂存）。",
        "args": {"path": "只看某个文件/目录，可省略",
                 "staged": "true 看已暂存差异"},
    },
    "git_log": {
        "description": "查看最近提交记录（一行一条）。",
        "args": {"limit": "条数，默认 10"},
    },
    "git_commit": {
        "description": "提交改动。默认只提交已跟踪文件的修改；"
                       "要包含新文件请显式给 paths。需要审批。",
        "args": {"message": "提交信息", "paths": "要提交的路径列表，可省略"},
    },
    "search_code": {
        "description": "按语义/关键词在代码库里检索相关片段（比 grep 更宽容，"
                       "适合「哪里处理了登录」这类模糊问题）。",
        "args": {"query": "想找的内容", "limit": "返回片段数，默认 5"},
    },
    "make_dir": {
        "description": "创建目录（含父级）。需要审批。",
        "args": {"path": "目录路径"},
    },
    "move_path": {
        "description": "移动或重命名文件/目录；dst 是已存在的目录时表示移入其中。"
                       "需要审批。",
        "args": {"src": "源路径", "dst": "目标路径或目标目录"},
    },
    "copy_path": {
        "description": "复制文件或目录（目录递归复制）。需要审批。",
        "args": {"src": "源路径", "dst": "目标路径或目标目录"},
    },
    "delete_path": {
        "description": "删除文件或目录。删目录必须 recursive=true。需要审批。",
        "args": {"path": "要删除的路径", "recursive": "目录必填 true"},
    },
    "list_checkpoints": {
        "description": "查看改写的快照记录（每次写入前自动保存）。",
        "args": {},
    },
    "undo_change": {
        "description": "撤销改动：把某个文件（或最近一批）还原到上次快照。"
                       "需要审批。",
        "args": {"path": "要还原的文件，省略则还原最近一批"},
    },
    "git_branch": {
        "description": "查看本地分支列表（当前分支带 *）。",
        "args": {},
    },
    "git_checkout": {
        "description": "切换分支；create=true 时新建并切过去。需要审批。",
        "args": {"branch": "分支名", "create": "true 表示新建"},
    },
    "git_push": {
        "description": "推送分支到远端（无上游时自动 -u）。需要审批。",
        "args": {"remote": "远端名，默认 origin", "branch": "分支名，默认当前分支"},
    },
    "git_pr": {
        "description": "用 GitHub CLI 创建 PR（需要已安装并登录 gh）。需要审批。",
        "args": {"title": "PR 标题", "body": "PR 描述，可省略"},
    },
    "mcp_list": {
        "description": "列出已注册 MCP 服务器的工具。",
        "args": {"server": "服务器名，省略则汇总全部"},
    },
    "mcp_call": {
        "description": "调用某个 MCP 服务器上的工具。需要审批。",
        "args": {"server": "服务器名", "tool": "工具名",
                 "arguments": "参数对象，可省略"},
    },
}


@dataclass
class ApprovalAsk:
    """一次待批准的写操作 / 命令。"""

    kind: str                 # write | shell
    target: str               # 文件相对路径或命令本身
    summary: str = ""         # 给用户看的一句话
    diff: str = ""            # 写类操作的差异预览（没有则空）
    tool: str = ""            # 发起审批的工具名（权限记忆按它记规则）


@dataclass
class ToolOutcome:
    """工具执行结果。"""

    ok: bool
    content: str
    tool: str = ""
    approved: Optional[bool] = None      # 需要审批时为 True/False，否则 None
    meta: Dict[str, Any] = field(default_factory=dict)


# 审批回调：返回 True 放行。为 None 时视为「自动放行」（库调用方的默认，
# CLI 会传入交互式实现，把决定权交回给用户）。
Approver = Callable[[ApprovalAsk], bool]


def _diff(old: str, new: str, path: str) -> str:
    """生成简洁的差异预览（最多 60 行，够判断改了什么）。"""
    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2))
    if not lines:
        return ""
    if len(lines) > 60:
        lines = lines[:60] + ["…（差异过长，已截断）"]
    return "\n".join(lines)


def _approved(approve: Optional[Approver], ask: ApprovalAsk) -> bool:
    if approve is None:
        return True
    try:
        return bool(approve(ask))
    except Exception:  # noqa: BLE001 — 审批回调异常一律按拒绝处理
        return False


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    return f"{head}\n…（输出过长，已截断，共 {len(text)} 字符）"


def describe_tools() -> str:
    """把工具清单渲染成 system prompt 里的说明文本。"""
    lines = []
    for name, spec in TOOL_SPECS.items():
        args = "；".join(f"{k}：{v}" for k, v in spec["args"].items())
        lines.append(f"- {name}：{spec['description']} 参数：{args}")
    return "\n".join(lines)


def execute_tool(name: str, args: Dict[str, Any], workspace: Workspace,
                 approve: Optional[Approver] = None,
                 settings: Any = None) -> ToolOutcome:
    """执行一个工具调用，异常一律转成 ``ok=False`` 的观察文本。

    不向上抛异常是刻意的：工具报错本身就是模型需要看到的反馈，
    只有让它看见"文件不存在""匹配不唯一"，它才可能自我纠正。
    """
    args = args if isinstance(args, dict) else {}
    try:
        if name == "list_files":
            entries = workspace.list_dir(str(args.get("path") or "."),
                                         int(args.get("depth") or 1))
            return ToolOutcome(True, "\n".join(entries) or "（空目录）", name)
        if name == "read_file":
            text = workspace.read_file(
                str(args.get("path") or ""),
                start=args.get("start"), end=args.get("end"))
            return ToolOutcome(True, text or "（空文件）", name)
        if name == "grep":
            hits = workspace.grep(
                str(args.get("pattern") or ""),
                str(args.get("path") or "."),
                str(args.get("glob") or ""),
                int(args.get("limit") or 60))
            return ToolOutcome(True, "\n".join(hits) or "（无匹配）", name)
        if name == "glob_files":
            hits = workspace.glob(str(args.get("pattern") or ""),
                                  int(args.get("limit") or 100))
            return ToolOutcome(True, "\n".join(hits) or "（无匹配）", name)
        if name == "write_file":
            return _write(workspace, args, approve)
        if name == "edit_file":
            return _edit(workspace, args, approve)
        if name == "run_command":
            return _shell(workspace, args, approve)
        if name == "repo_map":
            text = build_repo_map(workspace, str(args.get("path") or "."),
                                  int(args.get("max_files") or 300))
            return ToolOutcome(True, _truncate(text), name)
        if name == "git_status":
            return ToolOutcome(True, git_tools.status(workspace), name)
        if name == "git_diff":
            staged = bool(args.get("staged"))
            return ToolOutcome(
                True, git_tools.diff(workspace, str(args.get("path") or ""),
                                     staged=staged), name)
        if name == "git_log":
            return ToolOutcome(True, git_tools.log(workspace,
                                                   int(args.get("limit") or 10)),
                               name)
        if name == "git_commit":
            return _commit(workspace, args, approve)
        if name == "make_dir":
            return _make_dir(workspace, args, approve)
        if name == "move_path":
            return _move(workspace, args, approve)
        if name == "copy_path":
            return _copy(workspace, args, approve)
        if name == "delete_path":
            return _delete(workspace, args, approve)
        if name == "list_checkpoints":
            rows = workspace.checkpoints.list()
            if not rows:
                return ToolOutcome(True, "（还没有任何快照）", name)
            return ToolOutcome(True, "\n".join(
                f"{r['batch']} · {r['at']} · {len(r['files'])} 个文件"
                for r in rows), name)
        if name == "undo_change":
            return _undo(workspace, args, approve)
        if name == "search_code":
            return _search_code(workspace, args)
        if name == "git_branch":
            return ToolOutcome(True, git_tools.branches(workspace), name)
        if name == "git_checkout":
            return _checkout(workspace, args, approve)
        if name == "git_push":
            return _push(workspace, args, approve)
        if name == "git_pr":
            return _pr(workspace, args, approve)
        if name == "mcp_list":
            from .mcp_servers import list_tools as mcp_list

            return ToolOutcome(True, mcp_list(settings,
                                              str(args.get("server") or "")), name)
        if name == "mcp_call":
            return _mcp_call(args, approve, settings)
    except WorkspaceError as exc:
        return ToolOutcome(False, f"执行失败：{exc}", name)
    except (TypeError, ValueError) as exc:
        return ToolOutcome(False, f"参数有误：{exc}", name)
    return ToolOutcome(False, f"未知工具「{name}」", name)


# --------------------------------------------------------------------------- #
# 写类 / 执行类工具（需要审批）
# --------------------------------------------------------------------------- #
def _write(workspace: Workspace, args: Dict[str, Any],
           approve: Optional[Approver]) -> ToolOutcome:
    path = str(args.get("path") or "")
    content = str(args.get("content") or "")
    if not path:
        return ToolOutcome(False, "缺少参数 path", "write_file")
    target = workspace.resolve(path)
    rel = workspace.rel(target)
    existed = target.exists() and target.is_file()
    old = ""
    if existed:
        old = target.read_text(encoding="utf-8", errors="replace")
    ask = ApprovalAsk(
        kind="write", target=rel,
        summary=f"写入 {rel}（{len(content.splitlines())} 行）",
        diff=_diff(old, content, rel), tool="write_file")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次写入，文件未改动。",
                           "write_file", approved=False)
    # 先存快照再动手：新建文件也要建批次，撤销时才知道该删掉
    batch = workspace.checkpoints.snapshot([target], always=not existed)
    saved = workspace.write_file(path, content)
    if not existed:
        workspace.checkpoints.note_new(batch, saved)
    return ToolOutcome(True, f"已写入 {saved}（{len(content.splitlines())} 行）",
                       "write_file", approved=True)


def _edit(workspace: Workspace, args: Dict[str, Any],
          approve: Optional[Approver]) -> ToolOutcome:
    path = str(args.get("path") or "")
    old_text = str(args.get("old_text") or "")
    new_text = str(args.get("new_text") or "")
    if not path or not old_text:
        return ToolOutcome(False, "缺少参数 path 或 old_text", "edit_file")
    target = workspace.resolve(path)
    if not target.exists() or target.is_dir():
        return ToolOutcome(False, f"文件不存在：{path}", "edit_file")
    current = target.read_text(encoding="utf-8", errors="replace")
    count = current.count(old_text)
    if count != 1:
        return ToolOutcome(
            False,
            f"原文在「{path}」中出现 {count} 次，edit_file 要求唯一命中；"
            "请先 read_file 确认，或改用包含更多上下文的原文",
            "edit_file")
    updated = current.replace(old_text, new_text, 1)
    ask = ApprovalAsk(
        kind="write", target=workspace.rel(target),
        summary=f"修改 {workspace.rel(target)}",
        diff=_diff(current, updated, workspace.rel(target)), tool="edit_file")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次修改，文件未改动。",
                           "edit_file", approved=False)
    workspace.checkpoints.snapshot([target])
    saved = workspace.edit_file(path, old_text, new_text)
    return ToolOutcome(True, f"已修改 {saved}", "edit_file", approved=True)


# --------------------------------------------------------------------------- #
# 文件组织：建目录 / 移动 / 复制 / 删除 / 撤销
# --------------------------------------------------------------------------- #
def _make_dir(workspace: Workspace, args: Dict[str, Any],
              approve: Optional[Approver]) -> ToolOutcome:
    path = str(args.get("path") or "")
    if not path:
        return ToolOutcome(False, "缺少参数 path", "make_dir")
    rel = workspace.rel(workspace.resolve(path))
    ask = ApprovalAsk(kind="write", target=rel, summary=f"新建目录 {rel}",
                      tool="make_dir")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次操作，目录未创建。",
                           "make_dir", approved=False)
    saved = workspace.make_dir(path)
    return ToolOutcome(True, f"已创建目录 {saved}", "make_dir", approved=True)


def _move(workspace: Workspace, args: Dict[str, Any],
          approve: Optional[Approver]) -> ToolOutcome:
    src = str(args.get("src") or "")
    dst = str(args.get("dst") or "")
    if not src or not dst:
        return ToolOutcome(False, "缺少参数 src 或 dst", "move_path")
    src_rel = workspace.rel(workspace.resolve(src))
    dst_rel = workspace.rel(workspace.resolve(dst))
    ask = ApprovalAsk(kind="write", target=src_rel,
                      summary=f"移动 {src_rel} → {dst_rel}", tool="move_path")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次移动，文件未动。",
                           "move_path", approved=False)
    # 移动会同时改变源与目标的现场：源在快照里，目标位置由 undo 负责清掉
    batch = workspace.checkpoints.snapshot([workspace.resolve(src)], always=True)
    before, after = workspace.move(src, dst)
    workspace.checkpoints.note_new(batch, after)
    return ToolOutcome(True, f"已移动 {before} → {after}", "move_path",
                       approved=True)


def _copy(workspace: Workspace, args: Dict[str, Any],
          approve: Optional[Approver]) -> ToolOutcome:
    src = str(args.get("src") or "")
    dst = str(args.get("dst") or "")
    if not src or not dst:
        return ToolOutcome(False, "缺少参数 src 或 dst", "copy_path")
    src_rel = workspace.rel(workspace.resolve(src))
    dst_rel = workspace.rel(workspace.resolve(dst))
    ask = ApprovalAsk(kind="write", target=src_rel,
                      summary=f"复制 {src_rel} → {dst_rel}", tool="copy_path")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次复制，未产生副本。",
                           "copy_path", approved=False)
    batch = workspace.checkpoints.snapshot([workspace.resolve(dst)], always=True)
    before, after = workspace.copy(src, dst)
    workspace.checkpoints.note_new(batch, after)
    return ToolOutcome(True, f"已复制 {before} → {after}", "copy_path",
                       approved=True)


def _delete(workspace: Workspace, args: Dict[str, Any],
            approve: Optional[Approver]) -> ToolOutcome:
    path = str(args.get("path") or "")
    recursive = bool(args.get("recursive"))
    if not path:
        return ToolOutcome(False, "缺少参数 path", "delete_path")
    target = workspace.resolve(path)
    rel = workspace.rel(target)
    if target.is_dir():
        count = sum(1 for _ in workspace._iter_files(target))  # noqa: SLF001
        summary = f"删除目录 {rel}（含 {count} 个文件）"
    else:
        summary = f"删除文件 {rel}"
    ask = ApprovalAsk(kind="write", target=rel, summary=summary,
                      tool="delete_path")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次删除，文件还在。",
                           "delete_path", approved=False)
    workspace.checkpoints.snapshot([target])
    removed, count = workspace.delete(path, recursive=recursive)
    return ToolOutcome(True, f"已删除 {removed}（{count} 个文件）",
                       "delete_path", approved=True)


def _undo(workspace: Workspace, args: Dict[str, Any],
          approve: Optional[Approver]) -> ToolOutcome:
    path = str(args.get("path") or "")
    rel = workspace.rel(workspace.resolve(path)) if path else ""
    ask = ApprovalAsk(kind="write", target=rel or "最近一批",
                      summary=f"撤销改动：还原 {rel or '最近一批快照'}",
                      tool="undo_change")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次撤销。", "undo_change",
                           approved=False)
    try:
        detail = workspace.checkpoints.restore_latest(rel)
    except FileNotFoundError as exc:
        return ToolOutcome(False, str(exc), "undo_change")
    return ToolOutcome(True, f"已撤销：{detail}", "undo_change", approved=True)


def _search_code(workspace: Workspace, args: Dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolOutcome(False, "缺少参数 query", "search_code")
    from .codeindex import search_code

    hits = search_code(workspace, query, limit=int(args.get("limit") or 5))
    return ToolOutcome(True, "\n\n".join(hits) or "（没有相关片段）", "search_code")


# --------------------------------------------------------------------------- #
# 分支 / 推送 / PR / MCP（都需要审批）
# --------------------------------------------------------------------------- #
def _checkout(workspace: Workspace, args: Dict[str, Any],
              approve: Optional[Approver]) -> ToolOutcome:
    branch = str(args.get("branch") or "").strip()
    create = bool(args.get("create"))
    if not branch:
        return ToolOutcome(False, "缺少参数 branch", "git_checkout")
    ask = ApprovalAsk(kind="write", target=branch,
                      summary=f"{'新建并切换到' if create else '切换到'}分支 {branch}",
                      tool="git_checkout")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次分支切换。", "git_checkout",
                           approved=False)
    return ToolOutcome(True, git_tools.checkout(workspace, branch, create),
                       "git_checkout", approved=True)


def _push(workspace: Workspace, args: Dict[str, Any],
          approve: Optional[Approver]) -> ToolOutcome:
    remote = str(args.get("remote") or "origin")
    branch = str(args.get("branch") or "")
    ask = ApprovalAsk(kind="write", target=f"{remote}/{branch or '当前分支'}",
                      summary=f"推送到 {remote}/{branch or '当前分支'}",
                      tool="git_push")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次推送。", "git_push",
                           approved=False)
    return ToolOutcome(True, git_tools.push(workspace, remote, branch),
                       "git_push", approved=True)


def _pr(workspace: Workspace, args: Dict[str, Any],
        approve: Optional[Approver]) -> ToolOutcome:
    title = str(args.get("title") or "").strip()
    body = str(args.get("body") or "")
    if not title:
        return ToolOutcome(False, "缺少参数 title", "git_pr")
    ask = ApprovalAsk(kind="write", target=title,
                      summary=f"创建 PR：{title}", tool="git_pr")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次 PR 创建。", "git_pr",
                           approved=False)
    return ToolOutcome(True, git_tools.pr(workspace, title, body),
                       "git_pr", approved=True)


def _mcp_call(args: Dict[str, Any], approve: Optional[Approver],
              settings: Any) -> ToolOutcome:
    from .mcp_servers import call_tool as mcp_call

    if settings is None:
        from ..settings import get_settings

        settings = get_settings()
    server = str(args.get("server") or "").strip()
    tool = str(args.get("tool") or "").strip()
    arguments = args.get("arguments")
    arguments = arguments if isinstance(arguments, dict) else {}
    if not server or not tool:
        return ToolOutcome(False, "缺少参数 server 或 tool", "mcp_call")
    ask = ApprovalAsk(kind="shell", target=f"mcp:{server}/{tool}",
                      summary=f"调用 MCP 工具 {server} · {tool}",
                      tool="mcp_call")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次 MCP 调用。", "mcp_call",
                           approved=False)
    try:
        text = mcp_call(settings, server, tool, arguments)
    except Exception as exc:  # noqa: BLE001 — 服务器异常转成观察文本
        return ToolOutcome(False, f"MCP 调用失败：{exc}", "mcp_call")
    return ToolOutcome(True, text, "mcp_call", approved=True)


def _shell_env() -> Dict[str, str]:
    """命令环境：把「当前解释器所在目录」放到 PATH 首位。

    智能体活在这个 Python 进程里，用户的环境变量里未必有 ``python``；
    若不补这一下，模型跑测试只会得到 "command not found"，然后白白浪费几步
    去猜原因。只加路径，不改其它环境变量。
    """
    env = dict(os.environ)
    interpreter_dir = str(Path(sys.executable).parent)
    env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "")
    return env


def _commit(workspace: Workspace, args: Dict[str, Any],
            approve: Optional[Approver]) -> ToolOutcome:
    """提交改动（先审批，审批卡片里带上 status，避免盲签）。"""
    message = str(args.get("message") or "").strip()
    if not message:
        return ToolOutcome(False, "缺少参数 message", "git_commit")
    raw_paths = args.get("paths")
    paths = [str(p) for p in raw_paths] if isinstance(raw_paths, list) else []

    status_text = git_tools.status(workspace)
    scope = "、".join(paths) if paths else "已跟踪文件的改动"
    ask = ApprovalAsk(kind="write", target="git commit",
                      summary=f"提交 {scope}：{message}",
                      diff=status_text, tool="git_commit")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这次提交，仓库未改动。",
                           "git_commit", approved=False)
    saved = git_tools.commit(workspace, message, paths or None)
    return ToolOutcome(True, saved, "git_commit", approved=True)


def _shell(workspace: Workspace, args: Dict[str, Any],
           approve: Optional[Approver]) -> ToolOutcome:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolOutcome(False, "缺少参数 command", "run_command")
    try:
        timeout = max(1, min(int(args.get("timeout") or DEFAULT_TIMEOUT), 600))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    ask = ApprovalAsk(kind="shell", target=command,
                      summary=f"在 {workspace.root.name}/ 执行：{command}",
                      tool="run_command")
    if not _approved(approve, ask):
        return ToolOutcome(False, "用户拒绝了这条命令，未执行。",
                           "run_command", approved=False)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(workspace.root), env=_shell_env(),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolOutcome(False, f"命令超时（{timeout}s）已被终止", "run_command")
    except OSError as exc:
        return ToolOutcome(False, f"命令无法执行：{exc}", "run_command")

    body = (proc.stdout or "") + (proc.stderr or "")
    body = body.strip() or "（命令没有输出）"
    header = f"exit={proc.returncode}"
    return ToolOutcome(proc.returncode == 0,
                       f"{header}\n{_truncate(body)}", "run_command")


__all__ = [
    "ApprovalAsk", "ToolOutcome", "Approver", "TOOL_SPECS",
    "READONLY_TOOLS", "WRITE_TOOLS", "SHELL_TOOLS",
    "describe_tools", "execute_tool",
]
