"""``gameAgent`` 一键启动器：同时拉起 FastAPI 后端与 WPF 前端。

用法：``gameAgent``

流程：
1. 在子进程启动后端（``python -m gamedoctor.server``，默认 127.0.0.1:8765）。
2. 轮询 ``GET /`` 直到后端就绪。
3. 定位（必要时先用 ``dotnet build`` 构建）WPF 可执行文件并启动。
4. WPF 客户端退出后，结束后端进程。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
HEALTH_URL = f"http://{HOST}:{PORT}/"

# 允许用户用环境变量覆盖前后端位置（默认按仓库标准布局解析）。
_FRONTEND_EXE_NAME = "GameDoctor.Desktop.exe"


def _project_root() -> Path:
    """项目根目录（launcher.py 位于 <root>/src/gamedoctor/）。"""
    return Path(__file__).resolve().parents[2]


def _backend_ready(timeout: float = 30.0) -> bool:
    """轮询后端健康检查，直到返回 200 或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def _desktop_dir(root: Path) -> Path:
    """WPF 前端项目目录。"""
    override = os.environ.get("GAMEDOCTOR_DESKTOP_DIR")
    if override:
        return Path(override)
    return root / "desktop" / "GameDoctor.Desktop"


def _backend_log_path() -> Path:
    """后端日志文件：~/.gamedoctor/logs/server.log（追加写，含 500 堆栈）。"""
    log_dir = Path.home() / ".gamedoctor" / "logs"
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
    csproj = _desktop_dir(root) / "GameDoctor.Desktop.csproj"
    dotnet = shutil.which("dotnet")
    if not dotnet or not csproj.exists():
        return None
    print("[gameAgent] 未找到 WPF 可执行文件，正在构建 ...")
    subprocess.run([dotnet, "build", str(csproj), "-c", "Debug"], check=False)
    return _find_desktop_exe(root)


def main() -> int:
    """一键启动前后端，返回进程退出码。"""
    root = _project_root()

    print(f"[gameAgent] 启动后端 {HEALTH_URL}")
    log_path = _backend_log_path()
    print(f"[gameAgent] 后端日志：{log_path}")
    log_file = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    log_file.write(
        f"\n===== gameAgent backend started at "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
    )
    backend_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    backend = subprocess.Popen(
        [sys.executable, "-m", "gamedoctor.server"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=backend_env,
    )

    try:
        # 等待后端就绪
        if not _backend_ready():
            print("[gameAgent] 后端启动超时，请确认依赖：pip install -e '.[server]'")
            return 1
        print("[gameAgent] 后端已就绪")

        # 定位或构建 WPF 前端
        exe = _find_desktop_exe(root) or _build_desktop(root)
        if exe is None:
            print("[gameAgent] 未找到 WPF 前端，请安装 .NET SDK 后构建（见 README）")
            return 1

        print(f"[gameAgent] 启动前端 {exe}")
        frontend = subprocess.Popen([str(exe)], cwd=str(exe.parent))

        # 阻塞直到用户关闭 WPF 窗口
        frontend.wait()
    finally:
        print("[gameAgent] 关闭后端 ...")
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
        log_file.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())