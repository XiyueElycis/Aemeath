"""② 信息采集层（PRD §4.2 / §4.4）。

- :mod:`logs` —— 日志解析 + 主流引擎/平台日志路径映射表
- :mod:`files` —— 文件扫描（完整性 / 冲突 Mod / 缺失资源）+ 文本预览读取
- :mod:`env` —— 环境指纹（后台占用、磁盘、权限等）
"""

# 统一再导出三个子模块的公开接口，上层直接 `from analyzer import ...` 即可，
# 无需关心内部模块结构。
from .env import EnvironmentScanner, scan_environment
from .files import FileScanner, peek_text_file, scan_files
from .logs import LOG_PATH_MAP, LogParser, locate_logs, parse_logs

__all__ = [
    "LOG_PATH_MAP",
    "EnvironmentScanner",
    "FileScanner",
    "LogParser",
    "locate_logs",
    "parse_logs",
    "peek_text_file",
    "scan_environment",
    "scan_files",
]
