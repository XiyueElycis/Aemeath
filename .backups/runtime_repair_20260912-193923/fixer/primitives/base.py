"""修复原语基类与注册表（PRD §4.5.1）。

一个「修复原语」= 一种操作类型的最小可执行单元：

- :meth:`affected_paths` —— 声明将被改动的路径，供执行器**强制备份**；
- :meth:`apply` —— 执行动作（幂等、可回滚）；
- :meth:`verify` —— 执行后的可证伪检查点。

执行器（:mod:`gamedoctor.fixer.executor.transactional`）负责编排
「备份 → 执行 → 验证 → 回滚」，原语只关心"做什么"。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ...models import ActionLevel, ActionResult, TechStackFingerprint

# 全局注册表：原语名 -> 原语实例
REGISTRY: dict[str, "RepairPrimitive"] = {}


def register(cls: type["RepairPrimitive"]) -> type["RepairPrimitive"]:
    """类装饰器：把原语实例注册进 :data:`REGISTRY`（按 ``name`` 索引）。

    用法：``@register`` 标注在具体原语类上。执行器通过动作里的 ``primitive``
    字段名查表取实例，因此**新增原语只需写类 + 加装饰器**，无需改执行器。
    """
    inst = cls()
    REGISTRY[inst.name] = inst
    return cls


class RepairPrimitive(ABC):
    """修复原语基类。

    子类需声明类属性 ``name`` / ``category`` / ``default_level`` / ``summary``，
    并实现 :meth:`affected_paths` / :meth:`apply` / :meth:`verify`。
    """

    name: ClassVar[str]                      # 原语唯一名，如 "quarantine_file"
    category: ClassVar[str]                  # file / config / platform / runtime / system
    default_level: ClassVar[ActionLevel] = ActionLevel.L1_SAFE
    summary: ClassVar[str] = ""

    @abstractmethod
    def affected_paths(self, params: dict, fp: TechStackFingerprint) -> list[Path]:
        """返回将被改动的路径（供执行器备份）。无副作用。"""
        ...

    @abstractmethod
    def apply(self, params: dict, fp: TechStackFingerprint) -> ActionResult:
        """执行动作。实现应保证幂等、可回滚。

        失败要么抛异常、要么返回 ``FAILED`` 状态的 ``ActionResult``；执行器
        捕获到失败后会触发该动作的备份回滚。
        """
        ...

    @abstractmethod
    def verify(self, params: dict, fp: TechStackFingerprint) -> bool:
        """执行后的可证伪验证检查点：客观判断动作是否生效，通过返回 True。

        不通过时执行器会回滚该动作的备份，保证「凡改动必可还原」。
        """
        ...

    def preview(self, params: dict, fp: TechStackFingerprint) -> str:
        """可选重写：返回 dry-run 时展示的"将要怎么改"（如 diff 文本）。

        默认返回空串。实现必须是**只读**的：只读取将要改动的文件并算出结果，
        不得写盘，否则 dry-run 就名不副实了。
        """
        return ""


def get(name: str) -> RepairPrimitive:
    """按名取原语；未知原语抛 ``KeyError``。"""
    return REGISTRY[name]
