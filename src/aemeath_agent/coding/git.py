"""编程智能体 · Git 工具。

只封装最常用的四个动作，不重造版本控制：

- ``git_status`` / ``git_diff`` / ``git_log`` —— 只读，用于让模型看清「现在改了什么」；
- ``git_commit`` —— 写操作，必须过审批。

刻意不用 ``git add -A .``：那会把临时文件、日志、构建产物一起提交。默认只
提交**已被 Git 跟踪**的改动（``git add -u``），要新增文件必须显式给出 paths。
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional

from .workspace import Workspace, WorkspaceError

# 单次 git 命令超时（秒）
GIT_TIMEOUT = 60
# diff 输出上限：大改动的完整 diff 会直接撑爆上下文
MAX_DIFF_CHARS = 6_000


def _run(workspace: Workspace, *args: str) -> str:
    """在工作区根执行 git 子命令，返回输出（含 stderr）。"""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(workspace.root),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=GIT_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("未找到 git 命令，请确认已安装 Git") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git 命令超时（{GIT_TIMEOUT}s）") from exc
    body = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and not body.strip():
        raise WorkspaceError(f"git {' '.join(args)} 失败（exit={proc.returncode}）")
    return body.strip()


def _truncate(text: str) -> str:
    if len(text) <= MAX_DIFF_CHARS:
        return text
    return (text[:MAX_DIFF_CHARS]
            + f"\n…（diff 过长已截断，共 {len(text)} 字符；"
              "可用 git_diff 指定 path 只看某个文件）")


def git_available() -> bool:
    """本机是否装有 git（没装时所有 git 工具都要给出明确提示而不是报错）。"""
    return shutil.which("git") is not None


def is_repo(workspace: Workspace) -> bool:
    """工作区是否在某个 Git 仓库内。"""
    if not git_available():
        return False
    try:
        return _run(workspace, "rev-parse", "--is-inside-work-tree") == "true"
    except WorkspaceError:
        return False


def _unavailable() -> str:
    return "（未检测到 git 命令：请先安装 Git 并确保它在 PATH 中）"


def status(workspace: Workspace) -> str:
    """简短状态：当前分支 + 改动清单。"""
    if not git_available():
        return _unavailable()
    if not is_repo(workspace):
        return "（当前工作区不是 Git 仓库）"
    branch = _run(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    changes = _run(workspace, "status", "--short")
    return f"分支：{branch}\n{changes or '（工作区干净，没有未提交改动）'}"


def diff(workspace: Workspace, path: str = "", staged: bool = False) -> str:
    """工作区差异（默认未暂存，``staged`` 看已暂存）。"""
    if not git_available():
        return _unavailable()
    if not is_repo(workspace):
        return "（当前工作区不是 Git 仓库）"
    args: List[str] = ["diff"]
    if staged:
        args.append("--cached")
    args.append("--")
    if path:
        args.append(workspace.rel(workspace.resolve(path)))
    return _truncate(_run(workspace, *args) or "（没有差异）")


def log(workspace: Workspace, limit: int = 10) -> str:
    """最近提交（一行一条）。"""
    if not git_available():
        return _unavailable()
    if not is_repo(workspace):
        return "（当前工作区不是 Git 仓库）"
    limit = max(1, min(int(limit), 50))
    return _run(workspace, "log", f"-{limit}", "--oneline") or "（还没有提交）"


def commit(workspace: Workspace, message: str,
           paths: Optional[List[str]] = None) -> str:
    """提交改动。

    ``paths`` 为空时只暂存**已被跟踪**的改动（``git add -u``）；
    显式给出 paths 时按给出的路径暂存（可以包含新文件）。
    调用方负责先询问用户——本函数不内置审批。
    """
    if not is_repo(workspace):
        raise WorkspaceError("当前工作区不是 Git 仓库，无法提交")
    message = (message or "").strip()
    if not message:
        raise WorkspaceError("提交信息不能为空")

    before = _run(workspace, "status", "--short")
    if not before:
        return "（没有需要提交的改动）"

    if paths:
        rels = [workspace.rel(workspace.resolve(p)) for p in paths]
        _run(workspace, "add", "--", *rels)
    else:
        _run(workspace, "add", "-u")

    _run(workspace, "commit", "-m", message)
    head = _run(workspace, "log", "-1", "--oneline")
    return f"已提交：{head}"


def branches(workspace: Workspace) -> str:
    """列出本地分支，当前分支前加 ``*``。"""
    if not git_available():
        return _unavailable()
    if not is_repo(workspace):
        return "（当前工作区不是 Git 仓库）"
    current = _run(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    listing = _run(workspace, "branch", "--format=%(refname:short)")
    names = [n for n in listing.splitlines() if n.strip()]
    lines = [f"{'*' if n == current else ' '} {n}" for n in names]
    return "\n".join(lines) or "（还没有任何分支）"


def checkout(workspace: Workspace, branch: str, create: bool = False) -> str:
    """切换分支；``create=True`` 时以当前 HEAD 为基新建。"""
    if not is_repo(workspace):
        raise WorkspaceError("当前工作区不是 Git 仓库，无法切换分支")
    branch = (branch or "").strip()
    if not branch:
        raise WorkspaceError("分支名不能为空")
    if create:
        _run(workspace, "checkout", "-b", branch)
        return f"已创建并切换到分支 {branch}"
    _run(workspace, "checkout", branch)
    return f"已切换到分支 {branch}"


def push(workspace: Workspace, remote: str = "origin",
         branch: str = "") -> str:
    """推送当前（或指定）分支；没有上游时自动 ``-u``。"""
    if not is_repo(workspace):
        raise WorkspaceError("当前工作区不是 Git 仓库，无法推送")
    remote = (remote or "origin").strip() or "origin"
    target = branch.strip() or _run(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    # 没有上游分支时补 -u，否则首次推送会报 "no upstream branch"
    has_upstream = bool(_run(workspace, "rev-parse", "--abbrev-ref",
                             f"{target}@{{upstream}}").strip())
    args = ["push"] if has_upstream else ["push", "-u"]
    _run(workspace, *args, remote, target)
    return f"已推送到 {remote}/{target}"


def pr(workspace: Workspace, title: str, body: str = "") -> str:
    """用 GitHub CLI 创建 PR；没有 gh 或未登录时给出明确指引。

    刻意不自己调 GitHub API：那需要处理 token、仓库识别与权限，而 ``gh``
    已经把这些做对了；缺工具时如实告知，比失败得莫名其妙要好。
    """
    if shutil.which("gh") is None:
        raise WorkspaceError(
            "未检测到 gh（GitHub CLI），无法创建 PR。"
            "请先安装 gh 并执行 gh auth login，或手动在网页上开 PR。")
    title = (title or "").strip()
    if not title:
        raise WorkspaceError("PR 标题不能为空")
    args = ["pr", "create", "--title", title]
    if body.strip():
        args += ["--body", body]
    out = _run(workspace, *args)
    return out or "（gh 没有返回 PR 链接，请到仓库页面确认）"


__all__ = [
    "is_repo", "git_available", "status", "diff", "log", "commit",
    "branches", "checkout", "push", "pr",
]
