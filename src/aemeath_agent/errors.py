"""Aemeath Agent 自定义异常层次。

统一异常类型，便于上层捕获并给出一致的用户提示。
"""


class AemeathAgentError(Exception):
    """所有 Aemeath Agent 异常的基类。"""


class ConfigError(AemeathAgentError):
    """配置文件缺失 / 格式错误 / 密钥缺失。"""


class FingerprintError(AemeathAgentError):
    """技术栈指纹识别失败（引擎 / 运行时 / 平台无法识别）。"""


class UnsupportedBoundaryError(AemeathAgentError):
    """识别到私服 / 破解版 / 未发布测试版等超出支持边界的情况。"""


class KnowledgeError(AemeathAgentError):
    """本地知识库读写失败。"""


class SearchError(AemeathAgentError):
    """联网检索失败（各数据源均不可用或限流）。"""


class LLMError(AemeathAgentError):
    """LLM 调用失败 / 返回无法解析。"""


class SolverError(AemeathAgentError):
    """方案生成 / 自检失败，未能产出可执行动作序列。"""


class RepairError(AemeathAgentError):
    """自动修复执行失败（非预期的执行期错误）。"""


class BackupError(AemeathAgentError):
    """备份 / 回滚失败。"""


class TextEditError(AemeathAgentError):
    """文本文件编辑失败（编码不可识别、锚点未命中、写回失败等）。"""


class PolicyError(AemeathAgentError):
    """分级授权判定失败，或尝试执行禁止的动作。"""
