"""① 指纹识别层（PRD §4.1 F1）。

把"这是什么游戏"转化为"这是什么技术栈组合"：
- :mod:`engine` —— 引擎签名库与识别
- :mod:`runtime` —— 运行时 / 系统环境探测
- :mod:`platform` —— 平台适配层（Steam / Epic / GOG / 独立版）
"""

# 统一再导出指纹层三个子模块，掩盖内部模块结构，方便上层直接引用。
from .engine import EngineDetector, EngineSignature, detect_engine
from .platform import PlatformAdapter, detect_platform
from .runtime import RuntimeProbe, probe_runtime

__all__ = [
    "EngineDetector",
    "EngineSignature",
    "detect_engine",
    "RuntimeProbe",
    "probe_runtime",
    "PlatformAdapter",
    "detect_platform",
]
