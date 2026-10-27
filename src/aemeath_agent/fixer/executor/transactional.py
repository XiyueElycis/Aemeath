"""事务化执行器（PRD §4.5.3）。

编排「备份 → 执行 → 验证 → 失败回滚」闭环：

- **预检**：对每个动作做分级授权判定；
- **备份**：执行前对受影响路径强制备份；
- **执行**：L1 直跑，L2 逐项确认，L3 跳过仅给指引；
- **验证**：每步执行后跑对应检查点；
- **回滚**：任一步验证失败 → 还原该动作备份。

支持三种模式（:class:`ExecutionMode`）：
``auto`` / ``dry_run``（只输出不执行）/ ``step``（每步暂停确认）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Callable

from ...errors import RepairError
from ...models import (
    ActionLevel,
    ActionResult,
    ExecutionReport,
    FixStatus,
    RepairAction,
    RepairPlan,
    TechStackFingerprint,
)
from ..backup.manager import BackupManager
from ..policy.levels import AuthorizationPolicy, DefaultAuthorizationPolicy
from ..primitives.base import RepairPrimitive, get as get_primitive

# 确认回调：传入动作，返回是否执行（用于 L2 / 单步模式）
ConfirmFn = Callable[[RepairAction], bool]


class ExecutionMode(str, Enum):
    AUTO = "auto"
    DRY_RUN = "dry_run"
    STEP = "step"


class TransactionalExecutor:
    """事务化执行器。"""

    def __init__(
        self,
        policy: AuthorizationPolicy | None = None,
        backup_manager: BackupManager | None = None,
        confirm: ConfirmFn | None = None,
    ):
        self.policy = policy or DefaultAuthorizationPolicy()
        self.backup = backup_manager or BackupManager()
        self.confirm = confirm or (lambda _action: True)

    def execute(
        self,
        plan: RepairPlan,
        fp: TechStackFingerprint,
        mode: ExecutionMode = ExecutionMode.AUTO,
    ) -> ExecutionReport:
        report = ExecutionReport(diagnosis_id=plan.diagnosis_id)
        report.started_at = datetime.now()

        # 按优先级逐项执行（sorted_actions 已按 priority 升序）
        for action in plan.sorted_actions():
            level = self.policy.classify(action)  # 分级授权：可能强制升级 L3
            result = self._execute_one(action, fp, level, mode, plan.diagnosis_id)
            report.results.append(result)

            # 任一动作失败即停止后续：安全优先，不带着未知状态继续叠加改动
            if result.status == FixStatus.FAILED and mode != ExecutionMode.DRY_RUN:
                report.rolled_back = True
                break

        report.finished_at = datetime.now()
        return report

    def _execute_one(
        self,
        action: RepairAction,
        fp: TechStackFingerprint,
        level: ActionLevel,
        mode: ExecutionMode,
        diagnosis_id: str,
    ) -> ActionResult:
        """执行单个动作，贯穿「备份 → 执行 → 验证 → 失败回滚」。"""

        # 干跑：只描述将要做什么，绝不触碰文件。
        # 支持原语的可选 preview()：把"将要发生的 diff"渲染出来，做到先审后改。
        if mode == ExecutionMode.DRY_RUN:
            message = f"[dry-run] 将执行: {action.description}（级别 {level.value}）"
            detail = self._preview(action, fp)
            if detail:
                message = f"{message}\n{detail}"
            return ActionResult(action_id=action.id, status=FixStatus.PENDING, message=message)

        # L3 禁止自动：永不执行，仅返回人工指引（官方链接/静默参数/当前状态）
        if level == ActionLevel.L3_FORBIDDEN:
            guide = ""
            try:
                guide = get_primitive(action.primitive).guidance(action.params, fp)
            except Exception:  # noqa: BLE001 - 指引生成失败不影响拦截本身
                guide = ""
            message = f"L3 禁止自动执行，仅给指引: {action.description}"
            if guide:
                message = f"{message}\n{guide}"
            return ActionResult(
                action_id=action.id,
                status=FixStatus.NEEDS_MANUAL,
                message=message,
            )

        # L2 或单步模式：逐项询问用户，拒绝则跳过
        needs_confirm = (level == ActionLevel.L2_CONFIRM) or (mode == ExecutionMode.STEP)
        if needs_confirm and not self.confirm(action):
            return ActionResult(
                action_id=action.id, status=FixStatus.SKIPPED, message="用户跳过",
            )

        # 按动作里的 primitive 字段查注册表取原语实例；未知原语直接跳过（防御）
        try:
            primitive: RepairPrimitive = get_primitive(action.primitive)
        except KeyError:
            return ActionResult(
                action_id=action.id,
                status=FixStatus.SKIPPED,
                message=f"未知原语: {action.primitive}",
            )

        # 备份受影响路径：任何写操作前必须先备份（PRD §4.5.3 强制备份）
        backup_record = None
        try:
            affected = primitive.affected_paths(action.params, fp)
            if affected:
                backup_record = self.backup.backup(diagnosis_id, action.id, affected)
        except Exception as exc:  # noqa: BLE001 - 备份失败则不得执行，安全中止
            return ActionResult(
                action_id=action.id,
                status=FixStatus.FAILED,
                message=f"备份失败，中止: {exc}",
            )

        # 执行动作；抛异常视为失败并立即回滚
        try:
            apply_result = primitive.apply(action.params, fp)
        except Exception as exc:  # noqa: BLE001
            self._rollback_if_possible(backup_record)
            return ActionResult(action_id=action.id, status=FixStatus.FAILED, message=str(exc))

        # 验证检查点：未通过则回滚，保证"凡改动必可还原"
        verified = False
        try:
            verified = primitive.verify(action.params, fp)
        except Exception:  # noqa: BLE001 - 验证异常按未通过处理
            verified = False

        if not verified:
            self._rollback_if_possible(backup_record)
            apply_result.status = FixStatus.ROLLED_BACK
            apply_result.message = f"{apply_result.message}；验证未通过，已回滚"
            apply_result.verified = False
            return apply_result

        apply_result.action_id = action.id
        apply_result.verified = True
        # 验证通过：回填动作 ID、验证标记与备份路径，供报告留存追溯
        apply_result.backup_path = backup_record.backup_dir if backup_record else None
        return apply_result

    def _preview(self, action: RepairAction, fp: TechStackFingerprint) -> str:
        """调用原语的 preview() 生成改动预览；任何异常都降级为空串（不阻断 dry-run）。"""
        try:
            primitive = get_primitive(action.primitive)
        except KeyError:
            return ""
        try:
            return primitive.preview(action.params, fp)
        except Exception:  # noqa: BLE001 - 预览失败只影响可读性，不影响执行决策
            return ""

    def _rollback_if_possible(self, record) -> None:
        """尽量回滚指定备份；无备份记录则跳过，回滚失败抛 :class:`RepairError`。"""
        if record is None:
            return
        try:
            self.backup.rollback(record)
        except Exception as exc:  # noqa: BLE001
            raise RepairError(f"回滚失败: {exc}") from exc

    def rollback_all(self, diagnosis_id: str) -> None:
        """一键回滚：按时间倒序还原某诊断 ID 的所有备份。

        倒序还原保证"后改的先还原"，避免多个动作先后改同一文件时顺序错乱。
        """
        for record in reversed(self.backup.list_records(diagnosis_id)):
            self.backup.rollback(record)
