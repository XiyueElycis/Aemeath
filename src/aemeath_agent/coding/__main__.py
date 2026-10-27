"""命令行入口：``python -m aemeath_agent.coding "任务描述" --root .``

交互式审批：每个写文件 / 执行命令的动作都会打印差异或命令，等用户按 y 放行。
``--yes`` 跳过审批（明确表示信任本次任务）；``--dry-run`` 把写操作全部拒绝，
只让智能体做只读调查。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from .agent import RUN_TIMEOUT, CodingAgent
from .permissions import PermissionStore
from .sessions import SessionStore
from .tools import ApprovalAsk
from .workspace import Workspace, WorkspaceError


def _print_ask(ask: ApprovalAsk) -> None:
    """把待批准的动作展示成人能看懂的样子。"""
    print("\n" + "─" * 60)
    print(f"▸ 需要确认：{ask.summary}")
    if ask.diff:
        print("─" * 60)
        print(ask.diff)
    print("─" * 60)


def _make_approver(*, auto: bool, dry_run: bool,
                   permissions: PermissionStore):
    """构造审批回调；回答 a 会把这类操作记进「以后别再问」。

    非交互环境（无 stdin）默认拒绝——宁可让任务停下，也不要静默放行。
    """

    def approve(ask: ApprovalAsk) -> bool:
        if dry_run:
            print("\n[dry-run] 已拒绝：", ask.summary)
            return False
        if auto:
            return True
        _print_ask(ask)
        if not sys.stdin or not sys.stdin.isatty():
            print("（非交互环境，默认拒绝；如需放行请加 --yes）")
            return False
        try:
            answer = input("放行？[y] 是  [N] 否  [a] 以后都别再问  ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in ("a", "always"):
            permissions.remember(ask)
            print("（已记住：这类操作以后不再询问）")
            return True
        return answer in ("y", "yes")

    return approve


def _listener(kind: str, payload: dict) -> None:
    """把循环中的事件实时打印出来。"""
    if kind == "thought":
        print(f"\n💭 [{payload['step']}] {payload['text']}")
    elif kind == "action":
        print(f"🔧 [{payload['step']}] {payload['tool']} "
              f"{payload.get('args') or {}}")
    elif kind == "observation":
        status = "ok" if payload.get("ok") else "fail"
        text = (payload.get("text") or "").strip()
        if len(text) > 1200:
            text = text[:1200] + "\n…（已截断）"
        print(f"📄 [{payload['step']}] {status}\n{text}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aemeath-code",
        description="爱弥斯编程智能体：在工作区里读代码、改代码、跑命令。")
    parser.add_argument("task", nargs="*", help="任务描述（自然语言）")
    parser.add_argument("--root", default=".", help="工作区根目录，默认当前目录")
    parser.add_argument("--yes", action="store_true",
                        help="跳过审批，自动放行所有写入与命令")
    parser.add_argument("--dry-run", action="store_true",
                        help="只读模式：拒绝一切写入与命令")
    parser.add_argument("--max-steps", type=int, default=20,
                        help="最多步数，默认 20")
    parser.add_argument("--quiet", action="store_true", help="不打印过程")
    parser.add_argument("--resume", default="",
                        help="接着某个会话继续做（会话 id）")
    parser.add_argument("--sessions", action="store_true",
                        help="列出最近的会话后退出")
    parser.add_argument("--forget-permissions", action="store_true",
                        help="清空本工作区的长期放行规则后退出")
    return parser


def _print_sessions(store: SessionStore) -> None:
    rows = store.list(20)
    if not rows:
        print("（还没有任何会话）")
        return
    print("最近的会话：")
    for row in rows:
        status = "完成" if row["finished"] else "未完成"
        print(f"  {row['id']}  {row['at']}  {status}  {row['steps']} 步  "
              f"{row['task'][:40]}")


async def _amain(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        workspace = Workspace(Path(args.root))
    except WorkspaceError as exc:
        print(f"工作区不可用：{exc}")
        return 2

    permissions = PermissionStore(workspace.root)
    sessions = SessionStore()

    if args.sessions:
        _print_sessions(sessions)
        return 0
    if args.forget_permissions:
        permissions.reset()
        print(f"已清空放行规则：{permissions.path}")
        return 0

    task = " ".join(args.task).strip()
    if not task:
        print("请给出任务描述，例如：python -m aemeath_agent.coding "
              "\"给 settings.py 的 get_embedding 补一条注释\" --root .")
        print("提示：--sessions 查看历史会话，--resume <id> 接着做。")
        return 2

    print(f"工作区：{workspace.root}")
    print(f"任务：{task}")
    if args.resume:
        print(f"继续会话：{args.resume}")
    agent = CodingAgent(
        workspace,
        approve=_make_approver(auto=args.yes, dry_run=args.dry_run,
                               permissions=permissions),
        permissions=permissions,
        sessions=sessions,
        session_id=args.resume or None,
        max_steps=args.max_steps,
        on_event=None if args.quiet else _listener,
    )
    result = await asyncio.wait_for(agent.run(task), timeout=RUN_TIMEOUT + 30)

    print("\n" + "=" * 60)
    if result.finished:
        print(f"✅ 完成（{result.tool_calls} 步）\n{result.final}")
        code = 0
    else:
        print(f"⚠️ 未完成（{result.tool_calls} 步）\n{result.error}")
        code = 1
    if result.session_id:
        print(f"\n会话：{result.session_id}（可用 --resume 继续）")
    return code


def main(argv: Optional[list] = None) -> None:
    """控制台脚本入口。"""
    try:
        code = asyncio.run(_amain(argv))
    except KeyboardInterrupt:
        print("\n已中断。")
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
