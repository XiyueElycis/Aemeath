"""智能体基类定义。

所有智能体都继承自此基类，定义统一的接口和行为模式。
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from ..models import TechStackFingerprint, GameContext


class AgentCapability(Protocol):
    """智能体能力协议。"""

    def can_handle(self, context: GameContext) -> bool:
        """判断是否能够处理当前情境。"""
        ...

    def get_capabilities(self) -> List[str]:
        """返回智能体的能力列表。"""
        ...


@dataclass
class AgentTask:
    """智能体任务定义。

    :param priority: 优先级，0-100，越小越优先。
    :param agent: 路由目标智能体的注册名。缺省时用 ``name`` 路由。
        分离二者的原因：同一工作流可能多次调用同一智能体（如性能「基线 → 调优
        → 复测」），此时 ``name`` 需各自唯一以便区分结果与声明依赖，而 ``agent``
        保持指向同一个智能体。
    :param requires: 前置任务名列表。其中任一任务失败/被跳过时，本任务自动跳过。
        相比阶段级依赖，任务级依赖允许同一阶段内部分任务继续执行，
        避免一个只读采集失败就阻断整条下游链路。
    :param optional: 标记为可选任务时，其失败或被跳过不计入工作流整体成败，
        用于「装了更好、不影响主流程」的增益类智能体。
    """

    name: str
    priority: int = 0  # 优先级，0-100，越小越优先
    required_capabilities: List[str] = None
    context: GameContext = None
    data: Dict[str, Any] = None
    agent: str = ""
    requires: List[str] = None
    optional: bool = False

    def __post_init__(self):
        if self.required_capabilities is None:
            self.required_capabilities = []
        if self.requires is None:
            self.requires = []

    @property
    def route_to(self) -> str:
        """实际路由到的智能体名：显式 ``agent`` 优先，否则用 ``name``。"""
        return self.agent or self.name


@dataclass
class AgentResult:
    """智能体执行结果。

    :param skipped: 因前置依赖未满足而被门控跳过时为 ``True``。
        与「执行了但失败」区分开，便于报告如实呈现哪些环节没跑到。
    :param unverified: 本结果中**没有真实检测支撑**的条目（能力尚未接入、
        仅为占位推断时使用）。审议官验收复审会把它们汇总为带保留警告，
        回奏必须如实告知用户；切勿把假数据当作检测结论。
    """

    success: bool
    data: Any = None
    message: str = ""
    metrics: Dict[str, Any] = None
    warnings: List[str] = None
    errors: List[str] = None
    skipped: bool = False
    unverified: List[str] = None

    def __post_init__(self):
        if self.metrics is None:
            self.metrics = {}
        if self.warnings is None:
            self.warnings = []
        if self.errors is None:
            self.errors = []
        if self.unverified is None:
            self.unverified = []


class BaseAgent(abc.ABC):
    """智能体基类。"""

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(f"gamedoctor.agents.{name}")
        self.capabilities: List[str] = []
        self._initialized = False

    def initialize(self) -> None:
        """初始化智能体。"""
        if not self._initialized:
            self._do_initialize()
            self._initialized = True

    @abc.abstractmethod
    def _do_initialize(self) -> None:
        """具体的初始化逻辑，子类实现。"""
        pass

    @abc.abstractmethod
    async def execute(self, task: AgentTask) -> AgentResult:
        """执行任务。"""
        pass

    def can_handle(self, task: AgentTask) -> bool:
        """判断是否能够处理任务。"""
        # 检查所需能力
        if task.required_capabilities:
            return all(cap in self.capabilities for cap in task.required_capabilities)
        return True

    def get_capabilities(self) -> List[str]:
        """获取能力列表。"""
        return self.capabilities

    def validate_context(self, context: GameContext) -> bool:
        """验证上下文是否有效。"""
        return True

    def record_metric(self, name: str, value: Any) -> None:
        """记录性能指标。"""
        # 子类可以重写此方法实现指标记录
        pass

    def _report(self, kind: str, message: str, path: str = "") -> None:
        """上报一条操作事件，供 GUI 「操作过程可视化」展示。

        kind 可取 ``access``（访问目录/文件）、``modify``（修改）、``phase``（阶段）、
        ``info``（一般信息）。仅供需要可视化运行过程的智能体在 execute 内调用；
        不强制，缺省不报也完全可用。
        """
        from ..runtime import get_tracker

        get_tracker().log(kind, message, path)

    @staticmethod
    def _sandbox(data: Optional[Dict[str, Any]]):
        """从任务数据取当前沙箱会话；直接访问模式（未注入）返回 None。

        智能体落盘时统一 ``sandbox.io`` 原语并把本返回值作为首参，session 为
        None 时原语等价直接落盘，业务代码无需感知模式差异。
        """
        from ..sandbox import SandboxSession

        if not data:
            return None
        sx = data.get("_sandbox_session")
        return sx if isinstance(sx, SandboxSession) else None

    def log_error(self, message: str, exception: Optional[Exception] = None) -> None:
        """记录错误日志。"""
        self.logger.error(message, exc_info=exception)

    def log_warning(self, message: str) -> None:
        """记录警告日志。"""
        self.logger.warning(message)

    def log_info(self, message: str) -> None:
        """记录信息日志。"""
        self.logger.info(message)


class MultiAgentSystem:
    """多智能体系统管理器。"""

    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.task_queue: List[AgentTask] = []
        self.results: Dict[str, AgentResult] = {}

    def register_agent(self, agent: BaseAgent) -> None:
        """注册智能体。"""
        self.agents[agent.name] = agent
        agent.initialize()

    def select_agent(self, task: AgentTask) -> Optional[BaseAgent]:
        """为任务选择最合适的智能体；无匹配返回 ``None``。

        选择规则（确定性，同输入同结果）：

        1. **直接路由优先**：智能体名等于 ``task.route_to``，或命中
           ``required_capabilities`` 中任一项时优先，避免能力重叠
           （如 ``file_read`` 同时属于日志分析与脚本编辑）时选错。
        2. **专精优先**：能力集越窄越贴近本次请求，同等条件下选能力数最少的，
           防止"万能"智能体把专项任务抢走。
        3. **注册顺序兜底**：前两项并列时按注册顺序稳定排序，保证可复现。
        """
        required = set(task.required_capabilities or [])
        target = task.route_to
        candidates = [a for a in self.agents.values() if a.can_handle(task)]
        if not candidates:
            return None

        def _score(agent: BaseAgent) -> tuple[int, int, int]:
            direct = 0 if (agent.name == target or agent.name in required) else 1
            return (direct, len(agent.capabilities), self._order_of(agent.name))

        return min(candidates, key=_score)

    def _order_of(self, agent_name: str) -> int:
        """智能体的注册序号，用于稳定排序。"""
        for idx, name in enumerate(self.agents.keys()):
            if name == agent_name:
                return idx
        return len(self.agents)

    async def execute_task(self, task: AgentTask) -> AgentResult:
        """执行任务，选择合适的智能体（异步，等待智能体执行完成）。

        沙箱授权模式下（``_sandbox_session`` 经 :class:`AccessContext` 注入），
        每个任务执行前都先过用户授权门：批准才执行，拒绝/超时转成 skipped 结果，
        不抛异常、不产生非法状态转移。
        """
        # 沙箱模式：会话注入 + 任务级用户授权（工作流/HTTP 单跑/REPL 收口于此；
        # 协调器直办路径在调用本方法前另行过同一道门，以保留执行异常冒泡语义）
        from ..sandbox.context import gate_task

        skipped = await gate_task(task)
        if skipped is not None:
            self.results[task.name] = skipped
            return skipped

        selected_agent = self.select_agent(task)

        if selected_agent is None:
            caps = "、".join(task.required_capabilities) or "（未声明能力要求）"
            return AgentResult(
                success=False,
                message=f"No agent found for task: {task.name}（需要能力：{caps}）"
            )

        # 执行任务
        try:
            result = await selected_agent.execute(task)
            self.results[task.name] = result
            return result
        except Exception as e:  # noqa: BLE001
            return AgentResult(
                success=False,
                message=f"Task execution failed: {str(e)}",
                errors=[str(e)]
            )

    async def execute_workflow(self, tasks: List[AgentTask]) -> Dict[str, AgentResult]:
        """执行工作流（多个任务，串行，按 ``priority`` 升序）。

        ``priority`` 越小越先执行；同优先级按 ``name`` 稳定排序，保证
        同样的任务集合每次得到同样的执行顺序（安全前置类任务依赖这一点）。
        """
        results = {}
        for task in sorted(tasks, key=lambda t: (t.priority, t.name)):
            results[task.name] = await self.execute_task(task)
        return results