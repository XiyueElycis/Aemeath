"""诊断主流程编排（PRD §3 总体架构的六层串联）。

把指纹识别 → 信息采集 → 知识检索 → LLM 推理 → 自动修复 → 输出 串成一次
诊断。CLI 只负责参数解析与渲染，业务编排在此。

当前各层多为骨架，本模块保证流程可跑通；后续按层填充实现即可。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .analyzer.logs import locate_logs, parse_logs
from .config import Config, load
from .errors import GameDoctorError
from .fingerprint.engine import detect_engine
from .fingerprint.platform import detect_platform
from .fingerprint.runtime import probe_runtime
from .fixer.executor import ExecutionMode, TransactionalExecutor
from .knowledge.retriever import search_knowledge
from .models import (
    ConfidenceLevel,
    DiagnosisReport,
    ErrorCategory,
    ErrorReport,
    RepairPlan,
    SolverContext,
    SourceType,
    TechStackFingerprint,
)
from .searcher.adapters import SearchEngine
from .searcher.templates import build_queries
from .solver.critic import StructuralCritic
from .solver.generator import Solver

# --------------------------------------------------------------------------- #
# 错误分类（PRD §4.6：轻量模型 / 正则先行）
# --------------------------------------------------------------------------- #

_ERROR_PATTERNS: list[tuple[ErrorCategory, tuple[str, ...]]] = [
    (ErrorCategory.LAUNCH_FAILURE, ("0xc000007b", "missing dll", "entry point not found",
                                    "dll not found", "无法定位程序输入点", "缺少.*dll")),
    (ErrorCategory.RUNTIME_CRASH, ("access violation", "segfault", "segmentation fault",
                                   "exc_bad_access", "unhandled exception", "crash")),
    (ErrorCategory.RENDER_ERROR, ("black screen", "device lost", "tdr", "shader",
                                  "directx", "vulkan", "gpu")),
    (ErrorCategory.RESOURCE_LOAD, ("file not found", "corrupt", "checksum mismatch",
                                   "missing asset", "损坏")),
    (ErrorCategory.NETWORK, ("timeout", "nat type", "auth failed", "connection",
                             "eac", "battleye", "网络")),
    (ErrorCategory.PERFORMANCE, ("stutter", "low fps", "memory leak", "lag", "卡顿")),
    (ErrorCategory.SAVE_CONFIG, ("save corrupt", "settings reset", "cloud sync conflict",
                                 "存档", "配置丢失")),
    (ErrorCategory.COMPATIBILITY, ("wine", "proton", "rosetta", "arm", "兼容")),
]


def classify_error(message: str) -> ErrorReport:
    """正则/关键词先行分类，返回错误特征。

    按 :data:`_ERROR_PATTERNS` 顺序匹配，命中首个类别即停（类别互斥优先）。
    分类是"正则先行"，快且免费；后续 LLM 只对分类不清的样本兜底（PRD §4.6）。
    """
    low = message.lower()
    category = None
    for cat, kws in _ERROR_PATTERNS:
        if any(re.search(kw, low) for kw in kws):
            category = cat
            break
    return ErrorReport(
        category=category,
        raw_message=message,
        signature=_signature(message),
        confidence=ConfidenceLevel.MEDIUM,
        source=SourceType.AI_INFERRED,
    )


def _signature(message: str) -> str:
    """归一化错误签名：小写、地址占位、去空白、截断，用于知识库匹配。

    归一化目的：让"0x7ffe…"这类每次运行都不同的内存地址、多余空白不影响
    签名稳定性，从而不同玩家遇到的同类错误能命中同一条知识条目。
    """
    sig = re.sub(r"0x[0-9a-f]+", "<hex>", message.lower())  # 十六进制地址 → 占位符
    sig = re.sub(r"\s+", " ", sig).strip()                  # 折叠连续空白
    return sig[:120] or None                                # 截断，空串返回 None


# --------------------------------------------------------------------------- #
# 诊断编排
# --------------------------------------------------------------------------- #


class DiagnosisPipeline:
    """诊断主流程。"""

    def __init__(self, solver: Solver | None = None, critic=None,
                 config: Config | None = None):
        self.solver = solver  # 为 None 时使用默认 LLMSolver
        self.critic = critic or StructuralCritic()  # 结构性自检（P0 安全网）
        self.executor = TransactionalExecutor()
        self._config = config or load()

    def run(
        self,
        game_name: str,
        error_message: str = "",
        game_dir: Path | None = None,
        fix: bool = False,
        mode: ExecutionMode = ExecutionMode.AUTO,
    ) -> DiagnosisReport:
        # 诊断 ID：精确到秒，作为备份目录名与回滚/反馈的关联键
        diagnosis_id = datetime.now().strftime("%Y%m%d-%H%M%S")

        # ① 指纹识别：游戏 → 技术栈组合（引擎/运行时/平台）
        fp = self._fingerprint(game_name, game_dir)

        # ② 信息采集：错误分类 + 日志定位解析
        error = classify_error(error_message)
        if fp.engine:
            error.log_files = locate_logs(fp.engine.engine, game_name)

        # ③④ 知识检索 + 多源联网检索 → 汇聚上下文 → LLM 方案生成 + 结构自检
        ctx = self._gather_context(fp, error)
        plan, solve_err = self._solve(fp, error, diagnosis_id, ctx)

        # 结构性自检：剔除无效动作（未知原语/缺参/非法级别），issue 进风险提示
        review = self.critic.review(plan)
        if review.bad_action_ids:
            plan.actions = [a for a in plan.actions
                            if a.id not in review.bad_action_ids]

        report = DiagnosisReport(
            diagnosis_id=diagnosis_id,
            tech_stack=fp,
            error=error,
            plan=plan,
            references=ctx.searches[:5],
        )
        if solve_err is not None:
            report.risks.append(f"方案生成失败：{solve_err}")
        report.risks.extend(review.issues)

        # ⑤ 自动修复：只有 --fix 才进入事务化执行（dry-run/auto/step）
        if fix:
            report.execution = self.executor.execute(plan, fp, mode)

        return report

    def _fingerprint(self, game_name: str, game_dir: Path | None) -> TechStackFingerprint:
        # 引擎识别依赖安装目录（需扫文件）；运行时/平台探测与目录无关，始终执行
        engine = detect_engine(game_dir) if game_dir else None
        runtime = probe_runtime()
        platform = detect_platform(game_name, game_dir)
        return TechStackFingerprint(game_name=game_name, engine=engine, runtime=runtime,
                                    platform=platform)

    def _solve(self, fp: TechStackFingerprint, error: ErrorReport,
               diagnosis_id: str, ctx: SolverContext | None = None) -> tuple[RepairPlan, Exception | None]:
        """调用求解器生成修复计划，失败时降级为空计划。"""
        ctx = ctx or SolverContext()
        plan = RepairPlan()
        solve_err = None
        # 方案生成失败时降级为空计划（不阻断诊断输出），后续可提示用户手动排查
        try:
            if self.solver is not None:
                plan = self.solver.generate(fp, error, ctx)
            else:
                from .solver.generator import LLMSolver

                plan = LLMSolver().generate(fp, error, ctx)
        except Exception as e:
            solve_err = e
            plan = RepairPlan()
        plan.diagnosis_id = diagnosis_id  # 回填诊断 ID，供执行器/备份/反馈关联
        return plan, solve_err

    def _gather_context(self, fp: TechStackFingerprint, error: ErrorReport) -> SolverContext:
        """搜索知识库、网络并读取日志，聚合为求解上下文。"""
        searches: list[SearchResult] = []
        knowledge: list[KnowledgeHit] = []
        logs: list[LogEntry] = []

        # 联网检索：构建搜索查询并调用搜索引擎
        if fp.engine:
            queries = build_queries(fp, error.category, error.signature or "")
            try:
                engine = SearchEngine(self._config)
                searches = engine.search(queries)
            except Exception:
                searches = []  # 降级为空，不阻断流程

        # 知识库检索：按错误签名查本地知识
        try:
            # 基础知识检索
            basic_knowledge = search_knowledge(error.signature or error.raw_message)

            # 如果启用了向量检索，尝试 RAG 检索
            rag_knowledge = []
            if self._config.get("knowledge.use_vector", False):
                try:
                    from .knowledge.retriever import search_knowledge_with_rag
                    rag_query = f"{error.raw_message} {fp.game_name}"
                    rag_knowledge = search_knowledge_with_rag(
                        error.signature or error.raw_message,
                        fp.game_name,
                        rag_query
                    )
                except Exception:
                    rag_knowledge = []  # 降级为空

            # 合并基础知识和 RAG 结果
            knowledge = basic_knowledge
            if rag_knowledge:
                # 合并并去重
                all_knowledge = basic_knowledge + rag_knowledge
                seen = set()
                unique_knowledge = []
                for kh in all_knowledge:
                    if kh.key not in seen:
                        seen.add(kh.key)
                        unique_knowledge.append(kh)
                # 重新排序
                unique_knowledge.sort(key=lambda x: x.score, reverse=True)
                knowledge = unique_knowledge[:5]  # 限制为 top 5
        except Exception:
            knowledge = []  # 降级为空

        # 日志解析：定位并读取相关日志
        if error.log_files:
            try:
                logs = parse_logs(error.log_files)
            except Exception:
                logs = []

        return SolverContext(searches=searches, knowledge=knowledge, logs=logs)
