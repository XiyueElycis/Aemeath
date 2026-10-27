"""``Aemeath`` 一键启动器：同时拉起 FastAPI 后端与 WPF 前端。

用法：``Aemeath``

流程：
1. 预检端口：已有一个健康后端就直接复用；若是上次残留的僵尸后端则清理掉。
2. 在子进程启动后端（``python -m aemeath_agent.server``，默认 127.0.0.1:8765）。
3. 轮询 ``GET /`` 直到后端就绪（子进程中途死掉立即失败，并回显日志尾部）。
4. 定位（必要时先用 ``dotnet build`` 构建）WPF 可执行文件并启动。
5. WPF 客户端退出后，结束后端进程。

端口预检的存在理由：后端被强杀（关掉控制台窗口）时会留下仍占着 8765、
却不再响应的孤儿进程。此时新后端 bind 失败秒退，而健康检查又连不上，
旧版实现只会干等 30s 再报「请确认依赖已安装」——完全指错方向。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 与 aemeath_agent.server 读同一组环境变量，否则端口冲突时「换端口」的建议无效
HOST = os.environ.get("AEMEATH_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("AEMEATH_AGENT_PORT", "8765"))
HEALTH_URL = f"http://{HOST}:{PORT}/"

# 允许用户用环境变量覆盖前后端位置（默认按仓库标准布局解析）。
_FRONTEND_EXE_NAME = "AemeathAgent.Desktop.exe"


def _project_root() -> Path:
    """项目根目录（launcher.py 位于 <root>/src/aemeath_agent/）。"""
    return Path(__file__).resolve().parents[2]


def _probe() -> bool:
    """单次健康检查：后端返回 200 即视为活着。"""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _backend_ready(timeout: float = 30.0,
                   proc: subprocess.Popen | None = None) -> bool:
    """轮询后端健康检查，直到返回 200、超时，或（给了 proc 时）子进程已退出。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _probe():
            return True
        if proc is not None and proc.poll() is not None:
            return False  # 后端起不来已经死了，没必要空等
        time.sleep(0.3)
    return False


def _log_tail(path: Path, lines: int = 15) -> str:
    """读日志尾部若干行，用于把后端的真实报错回显给用户。"""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = content.rstrip().splitlines()[-lines:]
    return "\n".join(f"    │ {line}" for line in tail)


def _port_listener(port: int) -> tuple[int, list[str]] | None:
    """返回正在 LISTEN 该端口的 (pid, 命令行参数表)；没有则 None。

    psutil 取不到（权限受限等）时返回 None，调用方按「端口空闲」处理，
    后续 bind 失败仍会走子进程秒退的快路径。
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return None
    for conn in conns:
        if conn.status == psutil.CONN_LISTEN and conn.pid and conn.laddr \
                and conn.laddr.port == port:
            try:
                args = psutil.Process(conn.pid).cmdline()
            except (psutil.Error, OSError):
                args = []
            return conn.pid, args
    return None


def _is_our_backend(args: list[str]) -> bool:
    """命令行是否确凿是我们自己的后端。

    只认两种形态，避免误杀：仅仅是命令行里出现过 ``aemeath_agent.server`` 字样的
    无关进程（例如某个脚本内容里提到了它）不能算数——那会被我们自己杀掉。

    - ``python -m aemeath_agent.server``（启动器所用的形态）
    - 入口脚本 ``aemeath_agent-server`` / ``Aemeath``
    """
    if not args:
        return False
    for i, arg in enumerate(args[:-1]):
        if arg == "-m" and args[i + 1] == "aemeath_agent.server":
            return True
    exe = Path(args[0]).name.lower()
    return exe in {
        "aemeath_agent-server", "aemeath_agent-server.exe",
        "aemeath", "aemeath.exe",
    }


def _kill_tree(pid: int) -> None:
    """强杀进程及其子进程（后端自身是单进程，但保险起见连树一起清）。"""
    try:
        import psutil
        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            with contextlib.suppress(psutil.Error):
                child.kill()
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass  # 尽力而为；清不掉就让后续 bind 失败去报错


def _desktop_dir(root: Path) -> Path:
    """WPF 前端项目目录。"""
    override = os.environ.get("AEMEATH_AGENT_DESKTOP_DIR")
    if override:
        return Path(override)
    return root / "desktop" / "AemeathAgent.Desktop"


def _backend_log_path() -> Path:
    """后端日志文件：~/.aemeath_agent/logs/server.log（追加写，含 500 堆栈）。"""
    log_dir = Path.home() / ".aemeath_agent" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "server.log"


def _find_desktop_exe(root: Path) -> Path | None:
    """在 Debug / Release 输出目录中查找已构建的 WPF 可执行文件。"""
    for cfg in ("Debug", "Release"):
        exe = _desktop_dir(root) / "bin" / cfg / "net9.0-windows" / _FRONTEND_EXE_NAME
        if exe.exists():
            return exe
    return None


def _build_desktop(root: Path) -> Path | None:
    """前端未构建时，用 dotnet build 构建后返回可执行文件路径。"""
    csproj = _desktop_dir(root) / "AemeathAgent.Desktop.csproj"
    dotnet = shutil.which("dotnet")
    if not dotnet or not csproj.exists():
        return None
    print("〔爱弥斯〕还没有光幕载体，正在用代码编织 ...")
    subprocess.run([dotnet, "build", str(csproj), "-c", "Debug"], check=False)
    return _find_desktop_exe(root)


def main() -> int:
    """一键启动前后端，返回进程退出码。"""
    root = _project_root()

    print(f"〔爱弥斯〕星海信号塔点亮中，唤醒后端 {HEALTH_URL}")
    log_path = _backend_log_path()
    print(f"〔爱弥斯〕航行日志会记录在：{log_path}")

    # ── 端口预检 ───────────────────────────────────────────────────────
    # 复用已有后端，或清理上次残留的僵尸后端；否则新后端会 bind 失败秒退。
    backend: subprocess.Popen | None = None
    reuse = False  # 复用外部已有的健康后端：退出时不由我们关掉
    occupy = _port_listener(PORT)
    if occupy is not None:
        pid, args = occupy
        if _backend_ready(timeout=2.0):
            print(f"〔爱弥斯〕信号塔已在运行（PID {pid}），直接接管，不再另起后端")
            reuse = True
        elif _is_our_backend(args):
            print(f"〔爱弥斯〕发现残留的旧后端（PID {pid}）占着 {PORT} 却不回应，"
                  "正在清理 ...")
            _kill_tree(pid)
            if _port_listener(PORT) is not None:
                print(f"〔爱弥斯〕端口 {PORT} 仍被占用，请手动结束 PID {pid} 后重试")
                return 1
        else:
            print(f"〔爱弥斯〕端口 {PORT} 被别的程序占用（PID {pid}）："
                  f"{' '.join(args) or '未知进程'}")
            print("           请先结束它，或用 AEMEATH_AGENT_PORT 换一个端口")
            return 1

    if not reuse:
        log_file = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
        log_file.write(
            f"\n===== Aemeath backend awakened at "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
        )
        backend_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        backend = subprocess.Popen(
            [sys.executable, "-m", "aemeath_agent.server"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=backend_env,
        )

    try:
        # 等待后端就绪（子进程中途死掉会立即返回，不必空等满 30s）
        if not _backend_ready(proc=backend):
            if backend is not None and backend.poll() is not None:
                print(f"〔爱弥斯〕后端进程已退出（返回码 {backend.returncode}）。"
                      "日志尾部：")
                print(_log_tail(log_path))
                print("〔爱弥斯〕常见原因：端口被占用；或依赖缺失，请执行 "
                      "pip install -e '.[server]'")
            else:
                print("〔爱弥斯〕信号塔迟迟没有回应……请确认依赖已安装："
                      "pip install -e '.[server]'")
                print(_log_tail(log_path))
            return 1
        print("〔爱弥斯〕信号锚定！后端已就绪")

        # 定位或构建 WPF 前端
        exe = _find_desktop_exe(root) or _build_desktop(root)
        if exe is None:
            print("〔爱弥斯〕找不到光幕载体，请先安装 .NET SDK 后再构建"
                  "（见 README）")
            return 1

        print(f"〔爱弥斯〕展开星海光幕：{exe}")
        frontend = subprocess.Popen([str(exe)], cwd=str(exe.parent))

        # 阻塞直到用户关闭 WPF 窗口
        frontend.wait()
    finally:
        print("〔爱弥斯〕收起信号、熄灭光幕，关闭后端 ...")
        if backend is not None:
            # 连子进程一起收：只 terminate 主进程可能留下占着端口的孤儿
            with contextlib.suppress(OSError):
                backend.terminate()
            try:
                backend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_tree(backend.pid)
            log_file.close()
        else:
            print("（后端并非由本启动器拉起，保持运行）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())