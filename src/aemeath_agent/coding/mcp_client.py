"""编程智能体 · MCP 客户端（stdio 传输）。

MCP 的最小可用实现：启动一个本地进程，用**换行分隔的 JSON-RPC 2.0** 通信，
完成 ``initialize`` 握手，然后按需 ``tools/list`` 与 ``tools/call``。

只实现 stdio 一种传输：本项目是单机桌面场景，stdio 足够覆盖「本地工具服务器」
这个用途，HTTP/SSE 传输会引入端口与鉴权问题，收益不抵复杂度。

设计上的取舍：
- **读超时用后台线程 + 队列**，不用 ``select``——Windows 管道不支持 select；
- 每个请求带唯一 id，按 id 匹配响应，避免把通知与响应搞混；
- 进程退出或超时一律抛 :class:`MCPError`，由上层决定是否降级，
  绝不让一个坏服务器把整个任务挂死。
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from typing import Any, Dict, List, Optional

# 单次请求的默认超时（秒）
DEFAULT_TIMEOUT = 30.0
# 协议版本：客户端声明自己能说的版本
PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    """MCP 通信失败（进程退出、超时、协议错误）。"""


class StdioMCPClient:
    """一个 stdio MCP 服务器连接。

    :param command: 启动服务器的命令列表，如 ``["npx", "-y", "some-mcp"]``
    :param cwd: 服务器工作目录
    :param timeout: 单次请求超时
    """

    def __init__(self, command: List[str], *, cwd: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 env: Optional[Dict[str, str]] = None) -> None:
        if not command:
            raise MCPError("启动命令不能为空")
        self.command = list(command)
        self.cwd = cwd
        self.timeout = timeout
        self._env = env
        self._proc: Optional[subprocess.Popen] = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self.server_info: Dict[str, Any] = {}
        self.tools: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    def start(self) -> Dict[str, Any]:
        """启动进程并完成 initialize 握手，返回服务器信息。"""
        if self._proc is not None:
            return self.server_info
        try:
            self._proc = subprocess.Popen(
                self.command, cwd=self.cwd, env=self._env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise MCPError(f"无法启动 MCP 服务器：{exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "aemeath-coding", "version": "1.0"},
        })
        self.server_info = result.get("serverInfo") or {}
        # 握手最后一个通知，按 MCP 规范不期待响应
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.server_info

    def list_tools(self) -> List[Dict[str, Any]]:
        """列出服务器提供的工具。"""
        result = self._request("tools/list", {})
        self.tools = list(result.get("tools") or [])
        return self.tools

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None,
                  timeout: Optional[float] = None) -> str:
        """调用一个工具，把返回内容压成纯文本。"""
        result = self._request("tools/call",
                               {"name": name, "arguments": arguments or {}},
                               timeout=timeout)
        if result.get("isError"):
            raise MCPError(f"工具「{name}」返回错误：{_content_text(result)}")
        return _content_text(result)

    def close(self) -> None:
        """关闭连接（幂等）。"""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    def __enter__(self) -> "StdioMCPClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def _read_loop(self) -> None:
        """后台读线程：把每行塞进队列，EOF 时塞 None 作为结束信号。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._lines.put(None)
            return
        for line in proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, payload: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPError("MCP 连接未建立")
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"MCP 写入失败：{exc}") from exc

    def _request(self, method: str, params: Dict[str, Any],
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        """发一个请求并等待同 id 的响应。"""
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method,
                    "params": params})

        deadline = timeout if timeout is not None else self.timeout
        while True:
            try:
                line = self._lines.get(timeout=deadline)
            except queue.Empty:
                raise MCPError(
                    f"MCP 请求超时（{method}，{deadline}s）") from None
            if line is None:
                raise MCPError(f"MCP 服务器已退出（{method}）")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue                       # 非 JSON 行当噪声跳过
            if not isinstance(message, dict):
                continue
            if message.get("id") != req_id:
                continue                       # 通知 / 其它请求的响应
            if "error" in message:
                err = message["error"] or {}
                raise MCPError(
                    f"MCP 错误（{method}）：{err.get('message') or err}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}


def _content_text(result: Dict[str, Any]) -> str:
    """把 MCP 的 content 数组压成文本（text 直取，其它类型标注一下）。"""
    parts: List[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            parts.append(f"[{item.get('type') or 'unknown'}]")
    return "\n".join(parts) or "（无内容）"


__all__ = ["StdioMCPClient", "MCPError", "PROTOCOL_VERSION"]
