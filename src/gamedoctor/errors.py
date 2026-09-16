"""Game Doctor 自定义异常层次。

统一异常类型，便于上层捕获并给出一致的用户提示。
"""


class GameDoctorError(Exception):
    """所有 Game Doctor 异常的基类。"""


class ConfigError(GameDoctorError):
    """配置文件缺失 / 格式错误 / 密钥缺失。"""


class FingerprintError(GameDoctorError):
    """技术栈指纹识别失败（引擎 / 运行时 / 平台无法识别）。"""


class UnsupportedBoundaryError(GameDoctorError):
    """识别到私服 / 破解版 / 未发布测试版等超出支持边界的情况。"""


class KnowledgeError(GameDoctorError):
    """本地知识库读写失败。"""


class SearchError(GameDoctorError):
    """联网检索失败（各数据源均不可用或限流）。"""


class LLMError(GameDoctorError):
    """LLM 调用失败 / 返回无法解析。"""


class SolverError(GameDoctorError):
    """方案生成 / 自检失败，未能产出可执行动作序列。"""


class RepairError(GameDoctorError):
    """自动修复执行失败（非预期的执行期错误）。"""


class BackupError(GameDoctorError):
    """备份 / 回滚失败。"""


class TextEditError(GameDoctorError):
    """文本文件编辑失败（编码不可识别、锚点未命中、写回失败等）。"""


class PolicyError(GameDoctorError):
    """分级授权判定失败，或尝试执行禁止的动作。"""
