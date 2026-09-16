"""配置级修复原语（PRD §4.5.1 配置级）。

改配置文件、调启动参数、降渲染 API（-dx11）。配置文件路径/格式由引擎决定。
"""

from __future__ import annotations

from pathlib import Path

from ...errors import TextEditError
from ...models import ActionLevel, ActionResult, FixStatus, TechStackFingerprint
from ...textfile import TextDoc, unified_diff
from ..text_edit import detect_format, read_config_value, set_config_value
from .base import RepairPrimitive, register

# 单文件大小上限（2MB）：超过通常不是手写配置，误改风险远大于收益
_MAX_CONFIG_BYTES = 2 * 1024 * 1024


@register
class AppendLaunchArgPrimitive(RepairPrimitive):
    """追加启动参数（如 -dx11 降渲染）。对 .txt/.cfg 等行式文件追加参数行。"""

    name = "append_launch_arg"
    category = "config"
    default_level = ActionLevel.L1_SAFE
    summary = "追加启动参数"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        path = Path(params.get("config_path", ""))
        return [path] if path else []

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        path = Path(params.get("config_path", ""))
        args = params.get("args", [])
        if not path.exists() or not args:
            return ActionResult(status=FixStatus.FAILED, message=f"参数或路径无效: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        # 幂等：只追加尚未存在的参数，避免重复执行时写两遍
        added = [a for a in args if a not in text]
        if added:
            text += "\n" + "\n".join(added) + "\n"
            path.write_text(text, encoding="utf-8")
        return ActionResult(status=FixStatus.SUCCESS, message=f"已追加启动参数: {added}")

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        # 检查点：目标文件里确实包含全部参数
        path = Path(params.get("config_path", ""))
        if not path.exists():
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
        return all(a in text for a in params.get("args", []))


@register
class EditConfigPrimitive(RepairPrimitive):
    """改写配置文件指定键的值（支持 ini / json / kv 三种常见格式）。

    格式由后缀自动判定（``auto``），也可在 ``format`` 参数里显式指定。

    ini/kv 走**按行精改**（保留注释、空行、等号周围的空格写法），只有目标那一行
    会变；json 走解析后重排（JSON 无注释，代价可接受）。

    参数：
        path: 配置文件绝对路径（必填）
        key: 键名；json 格式支持点分嵌套路径，如 ``graphics.fps``
        value: 新值（字符串会自动推断数值/布尔）
        section: ini 小节名，可省略（表示小节之前的顶层区域）
        format: auto / ini / json / kv
        sep: kv 分隔符，``auto`` 时先试 ``=`` 再试 ``:``（Minecraft options.txt 用冒号）
        add_if_missing: 键不存在时是否追加，默认 True
    """

    name = "edit_config"
    category = "config"
    default_level = ActionLevel.L2_CONFIRM
    summary = "修改配置文件项"

    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        raw = params.get("config_path") or params.get("path") or ""
        return [Path(raw)] if raw else []

    def _target(self, params: dict) -> tuple[Path, str]:
        """取出目标路径（兼容 ``path`` / ``config_path``）与判定后的格式。"""
        raw = params.get("config_path") or params.get("path") or ""
        if not raw:
            raise TextEditError("动作缺少配置文件路径（path / config_path）")
        if not params.get("key"):
            raise TextEditError("动作缺少必填参数 key")
        path = Path(raw).expanduser()
        return path, detect_format(path, params.get("format", "auto"))

    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        path, fmt = self._target(params)
        if not path.is_file():
            return ActionResult(status=FixStatus.FAILED, message=f"配置文件不存在: {path}")
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            return ActionResult(status=FixStatus.FAILED, message=f"文件过大，拒绝改写: {path}")

        doc = TextDoc.load(path, params.get("encoding"))
        before = doc.text
        key, value = params["key"], params.get("value", "")

        try:
            after, hits = set_config_value(
                before, key, value, fmt=fmt,
                section=params.get("section"),
                sep=params.get("sep", "auto"),
                add_if_missing=bool(params.get("add_if_missing", True)),
            )
        except TextEditError as exc:
            return ActionResult(status=FixStatus.FAILED, message=str(exc))

        if after == before:
            # 幂等：目标值已是预期值，视为已修复，不重复写盘
            current = read_config_value(before, key, fmt=fmt, section=params.get("section"),
                                        sep=params.get("sep", "auto"))
            if current is not None and current == str(value):
                return ActionResult(status=FixStatus.SUCCESS, message=f"已是目标值，无需修改: {key}")
            return ActionResult(status=FixStatus.FAILED, message=f"未找到且未写入配置项: {key}")

        doc.write(after)
        action = "已新增" if hits == 0 else "已修改"
        return ActionResult(status=FixStatus.SUCCESS, message=f"{action}配置项 {key}={value}")

    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        """检查点：重新读回文件，目标键的值必须等于期望值。"""
        try:
            path, fmt = self._target(params)
        except TextEditError:
            return False
        if not path.is_file():
            return False
        try:
            text = TextDoc.load(path, params.get("encoding")).text
        except TextEditError:
            return False
        current = read_config_value(text, params["key"], fmt=fmt,
                                    section=params.get("section"),
                                    sep=params.get("sep", "auto"))
        return current is not None and current == str(params.get("value", ""))

    def preview(self, params: dict, fp: TechStackFingerprint) -> str:
        """dry-run 预览：输出将要发生改动的 diff。"""
        try:
            path, fmt = self._target(params)
        except TextEditError as exc:
            return f"（预览失败）{exc}"
        if not path.is_file():
            return f"（配置文件不存在）{path}"
        try:
            doc = TextDoc.load(path, params.get("encoding"))
            after, _ = set_config_value(
                doc.text, params["key"], params.get("value", ""), fmt=fmt,
                section=params.get("section"), sep=params.get("sep", "auto"),
                add_if_missing=bool(params.get("add_if_missing", True)),
            )
        except TextEditError as exc:
            return f"（预览失败）{exc}"
        return unified_diff(doc.text, after, label=str(path))
