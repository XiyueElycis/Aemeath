"""文本编辑修复原语（PRD §4.5.1 文件级 / 配置级）。

把"改一段文本"这件事做成**可被 LLM 直接产出、又被事务化执行器安全托管**的动作：

- LLM 只需给出 ``replace_text`` 动作 + 参数（路径、锚点、目标内容），无需关心
  编码/备份/回滚——后者由 :class:`TextDoc` 与事务执行器兜住；
- 执行前由 :meth:`affected_paths` 声明受影响文件 → 执行器**强制备份**；
- 执行后由 :meth:`verify` 跑可证伪检查点 → 不通过则自动回滚。

LLM 侧产出示例（放进 RepairAction.params）：

.. code-block:: json

    {
      "path": "C:/Games/MyGame/Config/options.txt",
      "old": "maxFps:260",
      "new": "maxFps:60",
      "regex": false,
      "expect": 1
    }
"""

from __future__ import annotations

from pathlib import Path

from ...errors import TextEditError
from ...models import ActionLevel, ActionResult, FixStatus, TechStackFingerprint
from ...textfile import TextDoc, unified_diff
from ..text_edit import replace_literal, replace_regex
from .base import RepairPrimitive, register


def _resolve(params: dict) -> Path:
    """取出并校验必填的 ``path`` 参数。"""
    raw = params.get("path") or ""
    if not raw:
        raise TextEditError("动作缺少必填参数 path")
    return Path(raw).expanduser()


@register
class ReplaceTextPrimitive(RepairPrimitive):
    """按锚点替换文本内容（支持字面量 / 正则），保持编码与行尾不变。

    安全设计：
    - **先算后写**：锚点未命中或命中次数不符预期时直接判定失败，**一个字节都不改**；
    - **幂等**：目标内容已存在时视为"已修复"，返回成功而不重复追加；
    - **锚点而非行号**：行号会随版本漂移，锚点字符串不会，这是自动修复稳定性的关键。
    """

    name = "replace_text"
    category = "text"
    default_level = ActionLevel.L2_CONFIRM
    summary = "按锚点替换文本内容"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        try:
            return [_resolve(params)]
        except TextEditError:
            return []  # 参数不合法时无路径可备份，执行阶段会报参数错误

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        path = _resolve(params)
        old = params.get("old", "")
        new = params.get("new", "")
        if not old:
            return ActionResult(status=FixStatus.FAILED, message="缺少替换锚点 old")

        doc = TextDoc.load(path, params.get("encoding"))
        before = doc.text

        # 幂等短路：内容已经是目标状态，直接判定成功（重复执行同一条修复不会叠加副作用）
        if new and old != new and old not in before and new in before:
            return ActionResult(status=FixStatus.SUCCESS, message=f"已处于目标状态，无需修改: {path}")

        try:
            if params.get("regex"):
                after, hits = replace_regex(
                    before, old, new,
                    count=int(params.get("count", 0)),
                    expect=params.get("expect"),
                    flags=int(params.get("flags", 0)),
                )
            else:
                after, hits = replace_literal(
                    before, old, new,
                    count=int(params.get("count", 0)),
                    expect=params.get("expect"),
                )
        except TextEditError as exc:
            # 锚点漂移 / 期望不符：拒绝改写，交由执行器（无改动，无需回滚）
            return ActionResult(status=FixStatus.FAILED, message=str(exc))

        if hits == 0:
            return ActionResult(status=FixStatus.FAILED, message=f"锚点未命中，未修改任何内容: {path}")

        doc.write(after)
        diff = unified_diff(before, after, label=str(path))
        changed = sum(1 for line in diff.split("\n")
                      if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
        return ActionResult(
            status=FixStatus.SUCCESS,
            message=f"已替换 {hits} 处，改动 {changed} 行: {path}",
        )

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        """检查点：目标内容必须出现；且（默认）旧内容必须消失。

        ``absent`` 可显式指定"改完后不应再出现的串"；未指定时，若本次是全量字面量
        替换且新旧串无重叠，则自动取 ``old`` 作为该串。
        """
        path = _resolve(params)
        if not path.is_file():
            return False
        text = TextDoc.load(path).text
        new = params.get("new", "")
        if new and new not in text:
            return False

        absent = params.get("absent")
        if absent is None:
            limited = int(params.get("count", 0) or 0) > 0
            old = params.get("old", "")
            if not params.get("regex") and not limited and old and old not in new:
                absent = old
        return not absent or absent not in text

    def preview(self, params: dict, fp: TechStackFingerprint) -> str:
        """dry-run 预览：把将要发生的改动渲染成 diff，先审后改。"""
        path = _resolve(params)
        if not path.is_file():
            return f"（文件不存在，执行时将报错）{path}"
        try:
            doc = TextDoc.load(path, params.get("encoding"))
            if params.get("regex"):
                after, _ = replace_regex(
                    doc.text, params.get("old", ""), params.get("new", ""),
                    count=int(params.get("count", 0)), flags=int(params.get("flags", 0)),
                )
            else:
                after, _ = replace_literal(
                    doc.text, params.get("old", ""), params.get("new", ""),
                    count=int(params.get("count", 0)),
                )
        except TextEditError as exc:
            return f"（预览失败）{exc}"
        return unified_diff(doc.text, after, label=str(path))
