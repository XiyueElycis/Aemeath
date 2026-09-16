"""采集主干：把指纹三件套接进治理链路。

指纹层（:mod:`gamedoctor.fingerprint`）此前只被旧管线 :mod:`gamedoctor.pipeline`
调用，三省六部治理链路构造的 :class:`~gamedoctor.models.GameContext` 只有
``game_name`` / ``install_path`` 两个字段。本模块统一完成一次采集并产出两份结果：

1. :class:`~gamedoctor.models.GameContext` —— 扁平字段，供进程内各智能体经
   ``task.context`` 直接读取；
2. ``fingerprint_dict`` —— 全部由 JSON 原生类型构成，挂到 ``task.data`` 上
   随任务跨 HTTP / 沙箱边界传递（dataclass 对象不可直接进 ``task.data``）。

铁律：任何探测失败都降级为 None / 默认值并记日志，**绝不向治理主链路抛异常**。
"""

from __future__ import annotations

import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .fingerprint import detect_engine, detect_platform, probe_runtime
from .models import (
    GameContext,
    PlatformInfo,
    RuntimeFingerprint,
    TechStackFingerprint,
)

logger = logging.getLogger(__name__)

# 目录体积统计的文件数上限，防止超大目录把一轮对话拖死
_DIR_SIZE_FILE_CAP = 20000

# AdapterRAM 是 uint32，真实显存 ≥4GiB 的卡会溢出到接近/超过 0xFFF00000，
# 落在该阈值以上（含 4GiB 整）的值不可信，按"未知"处理而非误报为 4GB。
_CIM_VRAM_DISTRUST_BYTES = 0xFFF00000


@lru_cache(maxsize=1)
def cached_runtime() -> RuntimeFingerprint:
    """运行时指纹（机器级信息）进程内只探测一次。

    PowerShell/CIM 子进程开销大，而同一进程服务的多轮请求机器环境不变，
    故用 lru_cache 收口。测试可用 ``cached_runtime.cache_clear()`` 复位。
    """
    return probe_runtime()


def build_fingerprint(game_name: str, game_dir: str | Path | None) -> TechStackFingerprint:
    """采集引擎 / 运行时 / 平台三段指纹；任一段失败独立降级。"""
    path = Path(game_dir) if game_dir else None
    dir_ok = bool(path is not None and path.is_dir())

    engine = None
    if path is not None and dir_ok:
        try:
            engine = detect_engine(path)
        except Exception:  # noqa: BLE001  # 引擎识别失败不阻断
            logger.warning("引擎识别失败，降级为未知：%s", path, exc_info=True)

    try:
        runtime = cached_runtime()
    except Exception:  # noqa: BLE001
        logger.warning("运行时探测失败，使用空运行时指纹", exc_info=True)
        runtime = RuntimeFingerprint()

    try:
        platform = detect_platform(game_name, path if dir_ok else None)
    except Exception:  # noqa: BLE001  # 平台库扫描可能因权限/编码失败
        logger.warning("平台识别失败，降级为 standalone", exc_info=True)
        platform = PlatformInfo()

    return TechStackFingerprint(
        game_name=game_name, engine=engine, runtime=runtime, platform=platform
    )


def fingerprint_to_dict(
    fp: TechStackFingerprint,
    game_dir: str,
    gpu_vram_mb: Optional[int],
) -> Dict[str, Any]:
    """把完整指纹摊平为可 ``json.dumps`` 的 dict（Path 转字符串）。"""
    rt = fp.runtime
    return {
        "fingerprint_ready": True,
        "game_dir": game_dir,
        # 引擎
        "engine": fp.engine.engine if fp.engine else None,
        "engine_version": fp.engine.version if fp.engine else None,
        "engine_confidence": fp.engine.confidence if fp.engine else None,
        # 平台
        "platform": fp.platform.platform,
        "app_id": fp.platform.app_id,
        "platform_install_path": str(fp.platform.install_path) if fp.platform.install_path else None,
        "library": fp.platform.library,
        # 运行时 / 系统
        "os": rt.os,
        "arch": rt.arch,
        "gpu": rt.gpu,
        "gpu_driver": rt.gpu_driver,
        "gpu_vram_mb": gpu_vram_mb,
        "gpu_api": rt.gpu_api,
        "directx_version": rt.directx_version,
        "directx_legacy": rt.directx_legacy,
        "dotnet": rt.dotnet,
        "dotnet_runtimes": list(rt.dotnet_runtimes),
        "vc_redist": list(rt.vc_redist),
        "vc_components": list(rt.vc_components),
        "java": rt.java,
        "wine_proton": rt.wine_proton,
    }


def build_context(
    game_name: str, game_dir: str | Path | None = ""
) -> Tuple[GameContext, Dict[str, Any]]:
    """一次采集，返回 ``(GameContext, fingerprint_dict)``。

    平台（Steam/Epic appmanifest）权威识别出的安装目录优先于手输目录；
    识别不到才用 ``game_dir`` 兜底。原始输入始终保留在 fingerprint_dict
    的 ``game_dir`` 字段中，便于审计。
    """
    game_dir = str(game_dir or "")
    fp = build_fingerprint(game_name, game_dir)
    rt = fp.runtime

    try:
        gpu_vram_mb = probe_gpu_vram_mb(rt.gpu)
    except Exception:  # noqa: BLE001
        logger.warning("显存探测失败，按未知处理", exc_info=True)
        gpu_vram_mb = None

    raw = Path(game_dir) if game_dir else None
    platform_path = fp.platform.install_path
    if fp.platform.platform != "standalone" and platform_path and platform_path.is_dir():
        install_path: Optional[Path] = platform_path
    else:
        install_path = raw if raw and raw.is_dir() else None

    size_mb = _dir_size_mb(install_path) if install_path else 0

    ctx = GameContext(
        game_name=game_name,
        install_path=install_path,
        platform=fp.platform.platform,
        app_id=fp.platform.app_id,
        size_mb=size_mb,
        engine=fp.engine.engine if fp.engine else None,
        gpu_name=rt.gpu,
        gpu_driver=rt.gpu_driver,
        gpu_vram_mb=gpu_vram_mb,
        directx_version=rt.directx_version,
        dotnet_version=rt.dotnet,
        vc_components=list(rt.vc_components),
        os_name=rt.os,
        os_arch=rt.arch,
        fingerprint_ready=True,
    )
    return ctx, fingerprint_to_dict(fp, game_dir, gpu_vram_mb)


def empty_fingerprint(game_dir: str = "") -> Dict[str, Any]:
    """无游戏目录（闲聊 / 纯问答）时的轻量占位指纹，不触发任何子进程。"""
    return {
        "fingerprint_ready": False,
        "game_dir": game_dir,
        "platform": "standalone",
        "engine": None,
        "gpu": None,
        "gpu_vram_mb": None,
    }


def _dir_size_mb(path: Path) -> int:
    """统计目录体积（MiB），文件数超过 :data:`_DIR_SIZE_FILE_CAP` 即停止。"""
    total = 0
    counted = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
            counted += 1
            if counted >= _DIR_SIZE_FILE_CAP:
                return round(total / (1024 * 1024))
    return round(total / (1024 * 1024))


@lru_cache(maxsize=8)
def probe_gpu_vram_mb(gpu_name: Optional[str] = None) -> Optional[int]:
    """探测独显显存（MiB）。取不到返回 ``None``（未知），绝不返回 0 充数。

    顺序：``nvidia-smi``（数值可靠）→ CIM ``AdapterRAM``（uint32 溢出值不可信）。
    机器级信息，按显卡名缓存；测试用 ``probe_gpu_vram_mb.cache_clear()`` 复位。
    """
    mb = _vram_from_nvidia_smi()
    if mb is not None:
        return mb
    return _vram_from_cim(gpu_name)


def _run_cmd(args: list[str], timeout: float = 8.0) -> Optional[str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _vram_from_nvidia_smi() -> Optional[int]:
    out = _run_cmd([
        "nvidia-smi",
        "--query-gpu=memory.total",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None
    values = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            values.append(int(line))
    # 多卡取最大值（独显通常远大于核显/虚拟卡）
    return max(values) if values else None


def _vram_from_cim(gpu_name: Optional[str]) -> Optional[int]:
    out = _run_cmd([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM | ConvertTo-Csv -NoTypeInformation)",
    ])
    if not out:
        return None
    rows = [ln for ln in out.strip().splitlines() if ln.strip()][1:]
    candidates: list[tuple[str, int]] = []
    for row in rows:
        cells = [c.strip('"') for c in row.split('","')]
        cells = [c.strip('"') for c in cells]
        if len(cells) < 2 or not cells[1].strip().isdigit():
            continue
        candidates.append((cells[0], int(cells[1])))
    if not candidates:
        return None

    target: Optional[tuple[str, int]] = None
    if gpu_name:
        low = gpu_name.lower()
        target = next((c for c in candidates if c[0].lower() == low), None)
        if target is None:
            target = next((c for c in candidates if c[0].lower() in low or low in c[0].lower()), None)
    if target is None:
        target = candidates[0]

    raw_bytes = target[1]
    if raw_bytes <= 0 or raw_bytes >= _CIM_VRAM_DISTRUST_BYTES:
        return None
    return round(raw_bytes / (1024 * 1024))
