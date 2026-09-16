"""平台适配层（PRD §4.1 平台适配层）。

区分 Steam / Epic / GOG / 独立版 / 模拟器：不同平台日志位置与修复手段不同
（Steam verify integrity vs Epic repair）。当前为接口 + 骨架。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import PlatformInfo


class PlatformAdapter(Protocol):
    """平台适配器协议。

    定义"平台识别"的最小接口，用于区分 Steam / Epic / GOG / 独立版等。
    """

    def detect(self, game_name: str, search_root: Path | None = None) -> PlatformInfo:
        """根据游戏名 / 安装位置识别所属平台。

        :param game_name: 游戏名称。
        :param search_root: 可选的安装根目录，用于定位平台特征文件；为 None 时由实现自行决定。
        :return: 识别结果 ``PlatformInfo``（平台类型 + 相应修复手段）。
        """
        ...


class DefaultPlatformAdapter:
    """默认实现骨架：仅返回独立版，具体识别逻辑留 TODO。

    后续接入真实探测（Steam/Epic/GOG 特征文件）后，再返回对应平台。
    """

    def detect(self, game_name: str, search_root: Path | None = None) -> PlatformInfo:
        # TODO: 探测 Steam（steamapps/appmanifest_*.acf）/ Epic（.egstore）/
        #       GOG（goggame-*.info）等平台特征文件
        # 未实现真实探测前，先按独立版（standalone）兜底返回
        return PlatformInfo(platform="standalone")


def detect_platform(game_name: str, search_root: Path | None = None) -> PlatformInfo:
    """便捷函数：用默认适配器识别游戏所属平台。"""
    return DefaultPlatformAdapter().detect(game_name, search_root)
