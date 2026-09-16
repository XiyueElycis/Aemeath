"""核心数据契约。

本模块统一定义跨层传递的数据结构（枚举 + dataclass），是各模块之间协作的
"共同语言"。原则：

- 枚举对应 PRD 中的分类学与分级，用 ``str`` 基类保证可 JSON 序列化。
- dataclass 均为"纯数据"，不含业务逻辑；序列化用 :func:`to_dict` /
  :func:`from_dict` 方便落地 SQLite / JSON 报告。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# 枚举：错误分类 / 授权分级 / 置信度 / 来源 / 执行状态
# --------------------------------------------------------------------------- #


class ErrorCategory(str, Enum):
    """通用错误分类学（PRD §4.2 F2）。"""

    RUNTIME_CRASH = "runtime_crash"      # Access Violation / Segfault / EXC_BAD_ACCESS
    LAUNCH_FAILURE = "launch_failure"    # Missing DLL / Entry Point Not Found / 0xc000007b
    RENDER_ERROR = "render_error"        # Black Screen / TDR / Device Lost / Shader Error
    RESOURCE_LOAD = "resource_load"      # File Not Found / Corrupt Asset / Checksum Mismatch
    NETWORK = "network"                  # Timeout / NAT Type / Auth Failed / EAC-BattlEye
    PERFORMANCE = "performance"          # Stutter / Low FPS / Memory Leak
    SAVE_CONFIG = "save_config"          # Save Corrupt / Settings Reset / Cloud Sync Conflict
    COMPATIBILITY = "compatibility"      # Wine/Proton / Rosetta / ARM 转译


class ActionLevel(str, Enum):
    """动作分级授权（PRD §4.5.2）。"""

    L0_READONLY = "L0"    # 只读检查，无需确认
    L1_SAFE = "L1"        # 安全自动：白名单，备份后直接执行
    L2_CONFIRM = "L2"     # 需确认：用户逐项确认后执行
    L3_FORBIDDEN = "L3"   # 禁止自动：永不执行，仅给指引


class ConfidenceLevel(str, Enum):
    """置信度分级。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceType(str, Enum):
    """结论来源分级（PRD §4.7 置信度分级输出）。"""

    OFFICIAL = "official"          # 官方文档确认
    COMMUNITY = "community"        # 社区高赞
    AI_INFERRED = "ai_inferred"    # AI 推理推测


class FixStatus(str, Enum):
    """单个修复动作的执行结果。"""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"            # 未执行（L3 或用户跳过）
    ROLLED_BACK = "rolled_back"
    NEEDS_MANUAL = "needs_manual"  # L3，需手动


# --------------------------------------------------------------------------- #
# 序列化工具
# --------------------------------------------------------------------------- #


def to_dict(obj: Any) -> Any:
    """递归把 dataclass / Enum / Path / datetime 转为 JSON 友好结构。"""
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def _from_dict(cls: type[Any], data: dict[str, Any]) -> Any:
    """按 dataclass 字段类型反序列化（支持 Enum 内建反向映射）。

    依据字段的**类型注解**决定如何还原：Enum 走 ``value`` 反向构造、Path 用
    字符串重建、``list[X]`` / ``tuple[X]`` 递归还原元素。
    """
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        target = f.type
        # 处理泛型容器 list[X] / tuple[X]：origin 是 list/tuple，__args__[0] 是元素类型
        origin = getattr(target, "__origin__", None)
        if origin in (list, tuple):
            inner = target.__args__[0]
            value = [_from_dict(inner, v) if dataclasses.is_dataclass(inner) else v for v in value]
        elif isinstance(target, type) and issubclass(target, Enum):
            value = target(value)  # 用 Enum 值反向构造，如 "L1" -> ActionLevel.L1_SAFE
        elif isinstance(target, type) and issubclass(target, Path):
            value = Path(value) if value is not None else None
        kwargs[f.name] = value
    return cls(**kwargs)


# --------------------------------------------------------------------------- #
# ① 指纹识别层（PRD §4.1 F1）
# --------------------------------------------------------------------------- #


@dataclass
class EngineFingerprint:
    """引擎特征。"""

    engine: str                       # unity / unreal / godot / renpy / ...
    version: str | None = None        # 如 "5.2"
    confidence: float = 1.0           # 识别置信度 [0, 1]


@dataclass
class RuntimeFingerprint:
    """运行时与系统环境探测结果（PRD §4.1 运行时探测）。"""

    os: str = ""                      # 如 "Windows 11"
    arch: str = ""                    # x64 / arm64
    directx_version: str | None = None
    vc_redist: list[str] = field(default_factory=list)   # 已安装 VC++ 注册表显示名（原始）
    # 规范化后的 VC++ 组件 id，如 "vc2015-2022_x64" / "vc2013_x86"
    vc_components: list[str] = field(default_factory=list)
    dotnet: str | None = None         # .NET Framework 版本（4.8 等）
    # .NET(Core) 已装运行时简表，如 "WindowsDesktop 8.0.4" / "NETCore 6.0.25"
    dotnet_runtimes: list[str] = field(default_factory=list)
    # DirectX 9 旧版托管扩展（d3dx9_43 / x3daudio / xinput 等，June 2010 运行库）
    # 是否已齐备：True=齐备 / False=缺失 / None=非 Windows 或未探测
    directx_legacy: bool | None = None
    java: str | None = None
    wine_proton: str | None = None
    gpu: str | None = None
    gpu_driver: str | None = None
    gpu_api: str | None = None        # DX12 / Vulkan / Metal


@dataclass
class PlatformInfo:
    """平台适配层（PRD §4.1 平台适配层）。"""

    platform: str = "standalone"      # steam / epic / gog / standalone / emulator
    app_id: str | None = None         # Steam AppID 等
    install_path: Path | None = None
    library: str | None = None        # 平台库名（如 Steam LibraryFolders）


@dataclass
class TechStackFingerprint:
    """① 游戏 → 技术栈组合 的完整指纹。"""

    game_name: str
    engine: EngineFingerprint | None = None
    runtime: RuntimeFingerprint = field(default_factory=RuntimeFingerprint)
    platform: PlatformInfo = field(default_factory=PlatformInfo)


# --------------------------------------------------------------------------- #
# ② 信息采集层（PRD §4.4 F4 日志定位 / 文件扫描）
# --------------------------------------------------------------------------- #


@dataclass
class LogEntry:
    """单条日志记录。"""

    source: Path                       # 日志文件路径
    line_no: int | None = None
    level: str | None = None           # error / warn / info
    message: str = ""
    timestamp: str | None = None


@dataclass
class ErrorReport:
    """解析出的错误特征（PRD §4.2 F2）。"""

    category: ErrorCategory | None = None
    raw_message: str = ""
    signature: str | None = None       # 归一化错误签名，用于知识库匹配
    log_files: list[Path] = field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    source: SourceType = SourceType.AI_INFERRED


# --------------------------------------------------------------------------- #
# ③④ 知识检索 + LLM 推理（PRD §4.3 / §4.6）
# --------------------------------------------------------------------------- #


@dataclass
class SearchResult:
    """单条检索结果。"""

    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""                   # tavily / serpapi / duckduckgo / pcgamingwiki ...
    rank: int = 0


@dataclass
class KnowledgeHit:
    """本地知识库命中条目（游戏指纹, 错误特征, 修复动作序列 三元组）。"""

    key: str = ""
    score: float = 0.0
    repair_template: str | None = None


@dataclass
class ReviewResult:
    """方案交叉自检结果。"""

    passed: bool = True
    issues: list[str] = field(default_factory=list)
    verdict: str = ""                  # 自检结论摘要
    bad_action_ids: list[str] = field(default_factory=list)  # 须剔除的动作 id


@dataclass
class SolverContext:
    """方案生成上下文：检索 + 知识库 + 日志摘要，由管线汇聚后传入求解器。

    把 ③知识检索层 与 ②信息采集层 的产物集中交给 ④LLM 推理层，使方案生成不再
    只依赖 (指纹, 错误) 两项，而能结合社区检索、本地知识命中与真实日志行。
    """

    searches: list[SearchResult] = field(default_factory=list)
    knowledge: list[KnowledgeHit] = field(default_factory=list)
    logs: list[LogEntry] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# ⑤ 自动修复层（PRD §4.5 F5）
# --------------------------------------------------------------------------- #


@dataclass
class RepairAction:
    """机器可执行的动作序列单元（原语 + 参数 + 验证检查点）。

    LLM 方案生成输出的最小单元，由执行引擎校验后落地。
    """

    id: str                            # 动作唯一 id，如 "a1"
    primitive: str                     # 原语名，见 fixer/primitives 注册表
    params: dict[str, Any] = field(default_factory=dict)  # 由技术栈指纹填参
    level: ActionLevel = ActionLevel.L1_SAFE
    description: str = ""              # 面向用户的说明
    verify_checkpoint: str = ""        # 可证伪的验证检查点
    priority: int = 0                  # 越小越优先


@dataclass
class RepairPlan:
    """修复计划：根因 + 按优先级排序的动作序列。"""

    diagnosis_id: str = ""
    category: ErrorCategory | None = None
    root_cause: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    source: SourceType = SourceType.AI_INFERRED
    actions: list[RepairAction] = field(default_factory=list)

    def sorted_actions(self) -> list[RepairAction]:
        """返回按优先级排序后的动作副本（不改动原列表顺序）。"""
        # 按 priority 升序（越小越优先），同优先级按 id 稳定排序
        return sorted(self.actions, key=lambda a: (a.priority, a.id))


@dataclass
class ActionResult:
    """单个动作执行结果。"""

    action_id: str = ""
    status: FixStatus = FixStatus.PENDING
    message: str = ""
    backup_path: Path | None = None
    verified: bool | None = None       # 验证检查点是否通过


@dataclass
class ExecutionReport:
    """一次修复事务的完整执行报告。"""

    diagnosis_id: str = ""
    results: list[ActionResult] = field(default_factory=list)
    rolled_back: bool = False          # 是否触发整体回滚
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def succeeded(self) -> int:
        """成功动作数。"""
        return sum(1 for r in self.results if r.status == FixStatus.SUCCESS)

    @property
    def failed(self) -> int:
        """失败动作数（不含跳过 / 回滚）。"""
        return sum(1 for r in self.results if r.status == FixStatus.FAILED)


# --------------------------------------------------------------------------- #
# ⑥ 输出层（PRD §5 输出报告格式）
# --------------------------------------------------------------------------- #


@dataclass
class GameContext:
    """游戏上下文信息。"""

    game_name: str = ""
    install_path: Path | None = None
    install_date: datetime | None = None
    last_played: datetime | None = None
    playtime_hours: float = 0.0
    platform: str = "standalone"  # steam/epic/gog/standalone
    app_id: str | None = None
    version: str | None = None
    size_mb: int = 0
    dlcs: list[str] = field(default_factory=list)
    # ---- 指纹层扁平回填（context_builder 采集，三省六部各智能体直读）---- #
    engine: str | None = None              # unity / unreal / godot / renpy ...
    gpu_name: str | None = None
    gpu_driver: str | None = None
    gpu_vram_mb: int | None = None         # None=未知（与 0 严格区分，判定层据此不表态）
    directx_version: str | None = None
    dotnet_version: str | None = None      # .NET Framework 版本
    vc_components: list[str] = field(default_factory=list)  # 规范化组件 id
    os_name: str = ""                      # 如 "Windows 11"
    os_arch: str = ""                      # x64 / arm64
    fingerprint_ready: bool = False       # 是否经过 context_builder 真实采集


@dataclass
class DiagnosisReport:
    """最终诊断报告（给输出层渲染）。"""

    diagnosis_id: str = ""
    tech_stack: TechStackFingerprint | None = None
    error: ErrorReport | None = None
    plan: RepairPlan | None = None
    execution: ExecutionReport | None = None
    references: list[SearchResult] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
