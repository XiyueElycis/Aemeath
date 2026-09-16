"""方案生成（PRD §4.6 方案生成）。

输出机器可执行的 :class:`~gamedoctor.models.RepairPlan`（原语 + 参数 + 验证检查点），
而非纯自然语言建议。自动修复要求动作须经执行引擎校验后才落地。

P0：把技术栈指纹 + 错误特征 + 日志摘要 + 知识库命中 + 检索结果拼进 prompt，要求
DeepSeek 输出结构化 JSON 动作序列，再解析回 :class:`RepairPlan`。解析全程手动构造
``RepairAction``（不依赖 ``models._from_dict``，因其受 PEP 563 注解字符串化影响无法
正确还原 Enum/Path）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from ..errors import SolverError
from ..llm.router import ModelRouter, Task
from ..models import (
    ActionLevel,
    ConfidenceLevel,
    ErrorReport,
    RepairAction,
    RepairPlan,
    SolverContext,
    SourceType,
    TechStackFingerprint,
)

_log = logging.getLogger("gamedoctor.solver")

# 导入即触发 @register，保证下方 REGISTRY 已填充所有内置原语
import gamedoctor.fixer.primitives  # noqa: F401
from ..fixer.primitives.base import REGISTRY  # noqa: E402

# 原语参数提示表（P0 侧表；P1 拟提升为原语类 ClassVar 单一来源）
# 键 = 原语名，值 = {"required": [...], "optional": [...]}
_PARAM_HINTS: dict[str, dict[str, list[str]]] = {
    "quarantine_file": {"required": ["path"], "optional": []},
    "clean_cache": {"required": ["path"], "optional": []},
    "append_launch_arg": {"required": ["config_path", "args"], "optional": []},
    "edit_config": {"required": ["key"],
                    "optional": ["config_path", "path", "value", "section", "format",
                                 "sep", "add_if_missing"]},
    "replace_text": {"required": ["path", "old"],
                     "optional": ["new", "regex", "count", "expect", "absent", "encoding"]},
    "steam_verify_integrity": {"required": [], "optional": ["app_id"]},
    "epic_repair": {"required": [], "optional": []},
    "install_runtime": {"required": ["component"], "optional": []},
}


class Solver(Protocol):
    """方案生成器协议。"""

    def generate(self, fp: TechStackFingerprint, error: ErrorReport,
                 ctx: SolverContext | None = None) -> RepairPlan:
        """基于技术栈指纹 + 错误特征 + 上下文，生成修复计划。"""
        ...


class LLMSolver:
    """基于 LLM 的默认实现。

    思路：把指纹 + 错误 + 检索/知识/日志上下文拼进 prompt，要求模型输出结构化
    ``RepairAction`` 序列，再解析回 :class:`RepairPlan`。解析失败抛
    :class:`SolverError`，由管线降级为空计划。
    """

    def __init__(self, router: ModelRouter | None = None):
        # 默认构造一个模型路由器；方案生成用强模型（见 Task.GENERATE 映射）
        self.router = router or ModelRouter()

    def generate(self, fp: TechStackFingerprint, error: ErrorReport,
                 ctx: SolverContext | None = None) -> RepairPlan:
        """基于指纹 + 错误特征 + 上下文生成修复计划。"""
        ctx = ctx or SolverContext()
        catalog = _build_primitive_catalog()
        prompt = _build_prompt(fp, error, ctx, catalog)
        raw = self.router.complete(Task.GENERATE, prompt)  # 失败抛 LLMError，由管线降级
        plan = _parse_plan(raw, error)
        # 命中本地知识库 → 来源标为社区（而非纯 AI 推理）
        if ctx.knowledge and plan.source == SourceType.AI_INFERRED:
            plan.source = SourceType.COMMUNITY
        return plan


# --------------------------------------------------------------------------- #
# 原语目录 / prompt 组装
# --------------------------------------------------------------------------- #


def _build_primitive_catalog() -> list[dict]:
    """遍历 REGISTRY 输出原语目录，供 prompt 约束 LLM 只用已注册原语。"""
    out: list[dict] = []
    for name, inst in REGISTRY.items():
        hints = _PARAM_HINTS.get(name, {"required": [], "optional": []})
        out.append({
            "name": inst.name,
            "category": inst.category,
            "default_level": inst.default_level.value,
            "summary": inst.summary,
            "required_params": hints["required"],
            "optional_params": hints["optional"],
        })
    return out


def _format_catalog(catalog: list[dict]) -> str:
    """把原语目录渲染成紧凑文本，塞进 prompt。"""
    lines: list[str] = []
    for c in catalog:
        req = ",".join(c["required_params"]) or "无"
        opt = ",".join(c["optional_params"]) or "无"
        lines.append(
            f"- {c['name']} ({c['category']}, {c['default_level']}): "
            f"{c['summary']}；必填 {req}；可选 {opt}"
        )
    return "\n".join(lines)


def _build_prompt(fp: TechStackFingerprint, error: ErrorReport,
                  ctx: SolverContext, catalog: list[dict]) -> str:
    """组装单条 prompt（指令 + 数据合一，P0 协议只收单字符串）。"""
    engine = f"{fp.engine.engine} {fp.engine.version or ''}".strip() if fp.engine else "未知"
    rt = fp.runtime
    pf = fp.platform

    # 日志摘要：取 error/fatal/exception 行，最多 8 条，每条截断 200 字符
    log_lines = [e for e in ctx.logs if e.level in ("error", "fatal", "exception")][:8]
    log_block = "\n".join(
        f"- {e.source}:{e.line_no}: {e.message[:200]}" for e in log_lines
    ) or "（无可用日志）"

    kb_block = "\n".join(
        f"- {h.repair_template}" for h in ctx.knowledge if h.repair_template
    ) or "（无本地知识命中）"

    sr_block = "\n".join(
        f"[{i+1}] {r.title} — {r.snippet[:300]}" for i, r in enumerate(ctx.searches[:5])
    ) or "（无检索结果）"

    return f"""你是游戏故障修复专家 Game Doctor。基于下方技术栈指纹、错误特征、日志、知识库命中与检索结果，产出可执行的修复方案。

【输出契约】
- 只输出一个 JSON 对象，不要任何解释、前后缀文字、markdown 代码围栏。
- 结构：{{"root_cause": "根因一句话", "confidence": "high|medium|low", "actions": [...]}}
- 每个 action：{{"id": "a1", "primitive": "<原语名>", "params": {{...}}, "level": "L0|L1|L2|L3", "description": "面向用户的说明", "verify_checkpoint": "可证伪的验证点", "priority": 0}}
- 硬约束：
  1. primitive 必须来自下方【原语目录】，不得自创。
  2. params 中的路径必须是绝对路径（Windows 形如 C:/... 或 C:\\\\...）。
  3. level：L0 只读 / L1 安全自动 / L2 需确认 / L3 仅指引（注册表/系统DLL/驱动等高危用 L3）。
  4. 每个 action 必须有 verify_checkpoint（客观可验证，如"配置文件中存在 -dx11"）。
  5. priority 越小越优先。
  6. 没把握就少给动作，宁可空 actions 也不要编造。

【原语目录】
{_format_catalog(catalog)}

【技术栈】
游戏: {fp.game_name}
引擎: {engine}
平台: {pf.platform} app_id={pf.app_id or '未知'} install_path={pf.install_path or '未知'}
运行时: os={rt.os or '未知'} arch={rt.arch or '未知'} gpu={rt.gpu or '未知'} directx={rt.directx_version or '未知'} vc_redist={','.join(rt.vc_redist) or '未知'} dotnet={rt.dotnet or '未知'} java={rt.java or '未知'} wine_proton={rt.wine_proton or '无'}

【错误特征】
类别: {error.category.value if error.category else '未知'}
签名: {error.signature or '无'}
原始信息: {error.raw_message or '(空)'}

【日志摘要】（error/fatal 行）
{log_block}

【知识库命中】（已验证修复模板，优先采纳）
{kb_block}

【检索结果】（社区参考，需甄别）
{sr_block}

请输出 JSON 对象。"""


# --------------------------------------------------------------------------- #
# JSON 解析
# --------------------------------------------------------------------------- #


def _extract_json(text: str) -> dict:
    """从模型输出里提取 JSON 对象：先直解，再剥围栏，再取最外层花括号子串。"""
    t = text.strip()
    # 剥 ```json ... ``` 围栏
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.MULTILINE).strip()
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else {"actions": data}
    except json.JSONDecodeError:
        pass
    # 兜底：取最外层 { ... } 子串
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {"actions": data}
        except json.JSONDecodeError:
            pass
    raise SolverError("LLM 方案解析失败：输出非合法 JSON")


def _coerce_level(value, default: ActionLevel) -> ActionLevel:
    """把 LLM 给的 level 值还原为 ActionLevel，非法回退默认。"""
    if isinstance(value, ActionLevel):
        return value
    try:
        return ActionLevel(str(value)) if value else default
    except ValueError:
        return default


def _parse_plan(raw: str, error: ErrorReport) -> RepairPlan:
    """解析 LLM 输出为 :class:`RepairPlan`。

    未知原语 → 丢弃并记日志；缺 id → 自动补；非法 level → 用原语默认；
    非法 JSON → 抛 :class:`SolverError`（被管线降级为空计划）。
    """
    data = _extract_json(raw)
    # LLM 可能直接返回 actions 数组
    if isinstance(data, list):
        raw_actions, root_cause = data, ""
    else:
        raw_actions = data.get("actions", [])
        root_cause = str(data.get("root_cause", ""))
        if isinstance(raw_actions, dict):  # 容错：{actions:{...}} 误写
            raw_actions = [raw_actions]
    if not isinstance(raw_actions, list):
        raw_actions = []

    actions: list[RepairAction] = []
    dropped: list[str] = []
    for i, a in enumerate(raw_actions):
        if not isinstance(a, dict):
            continue
        prim = str(a.get("primitive", ""))
        if prim not in REGISTRY:
            dropped.append(f"未知原语 '{prim}'(动作 {a.get('id', '?')})")
            continue
        inst = REGISTRY[prim]
        params = a.get("params") if isinstance(a.get("params"), dict) else {}
        actions.append(RepairAction(
            id=str(a.get("id") or f"a{i + 1}"),
            primitive=prim,
            params=params,
            level=_coerce_level(a.get("level"), inst.default_level),
            description=str(a.get("description", "")),
            verify_checkpoint=str(a.get("verify_checkpoint", "")),
            priority=max(0, min(1000, int(a.get("priority", 0) or 0))),
        ))
    if dropped:
        _log.warning("LLM 方案丢弃动作: %s", "; ".join(dropped))

    # confidence 还原，非法回退 MEDIUM
    conf_raw = str(data.get("confidence", "medium")) if isinstance(data, dict) else "medium"
    try:
        confidence = ConfidenceLevel(conf_raw)
    except ValueError:
        confidence = ConfidenceLevel.MEDIUM

    return RepairPlan(
        diagnosis_id="",
        category=error.category,
        root_cause=root_cause or error.raw_message,
        confidence=confidence,
        source=SourceType.AI_INFERRED,
        actions=actions,
    )
