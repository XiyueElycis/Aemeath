"""游戏安全防护智能体。

安全防护：反作弊兼容性检查、恶意软件检测、隐私保护、安全更新提醒。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext

# 常见反作弊（EAC / BattlEye / VAC / Vanguard / Ricochet）的文件特征
# 每项：展示名 → 安装目录内可能出现的文件名/目录名片段（小写匹配）
_ANTI_CHEAT_FILE_MARKERS = {
    "EasyAntiCheat (EAC)": [
        "easyanticheat", "easy anti-cheat",
        "easyanticheat_eos.exe", "easyanticheat_setup.exe", "eac_start.exe",
    ],
    "BattlEye": [
        "battleye", "beclient_x64.dll", "beclient.dll",
        "beservice_x64.exe", "beservice.exe",
    ],
    "Riot Vanguard": ["vgc.exe", "vgtray.exe", "vgk.sys"],
    "Ricochet (使命召唤)": ["ricochet.exe", "ricochet"],
}

# 正在运行时也可能出现的反作弊服务进程（小写）
_ANTI_CHEAT_PROCESS_MARKERS = {
    "EasyAntiCheat (EAC)": ["easyanticheat_eos.exe", "easyanticheat.exe"],
    "BattlEye": ["beservice_x64.exe", "beservice.exe", "beclient_x64.exe"],
    "Riot Vanguard": ["vgc.exe", "vgtray.exe"],
}

# 高风险进程关键字（实际枚举系统进程时匹配）
_SUSPICIOUS_KEYWORDS = ["keylogger", "injector", "cheatengine", "cheat engine",
                        "xenos", "extremedumper", "process hacker"]


@dataclass
class SecurityResult:
    """安全防护结果。"""

    game_name: str
    anti_cheat_detected: bool = False
    anti_cheat_names: List[str] = field(default_factory=list)
    anti_cheat_conflicts: List[str] = field(default_factory=list)
    suspicious_processes: List[str] = field(default_factory=list)
    privacy_issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class SecurityAgent(BaseAgent):
    """安全防护智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("security_agent", config)
        self.capabilities = [
            "security", "anti_cheat", "malware_detection", "privacy_protection",
        ]
        self.anti_cheat_check = self.config.get("anti_cheat_check", True)
        self.scan_frequency = self.config.get("scan_frequency", "daily")

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Security Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")
            context: GameContext = data.get("game_context")

            ac_names = self._detect_anti_cheat(context) if self.anti_cheat_check else []
            conflicts = self._check_anti_cheat_conflicts(ac_names)
            suspicious = self._scan_suspicious_processes()
            privacy = self._check_privacy()

            result = SecurityResult(
                game_name=game_name,
                anti_cheat_detected=bool(ac_names),
                anti_cheat_names=ac_names,
                anti_cheat_conflicts=conflicts,
                suspicious_processes=suspicious,
                privacy_issues=privacy,
                recommendations=self._build_recommendations(ac_names, conflicts, suspicious, privacy),
            )

            self.record_metric("suspicious_process_count", len(suspicious))
            return AgentResult(
                success=True,
                data=result,
                message=f"Security check completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Security check failed", e)
            return AgentResult(
                success=False,
                message=f"Security check failed: {e}",
                errors=[str(e)],
            )

    def _detect_anti_cheat(self, context: GameContext) -> List[str]:
        """通过安装目录文件特征 + 运行中服务进程识别反作弊组件。

        返回识别到的反作弊展示名列表（去重）。两路证据互为补充：
        游戏未运行时文件特征仍可识别；游戏已启动时进程特征更直接。
        """
        found: set[str] = set()

        # 1. 文件特征：扫安装目录下两层的文件名/目录名
        install_path = None
        if context is not None:
            install_path = getattr(context, "install_path", None)
        if install_path:
            root = Path(str(install_path))
            if root.exists():
                try:
                    entries = [root, *list(root.iterdir())]
                    # 再深入一层常见的 bin/游戏目录
                    for sub in list(root.iterdir())[:50]:
                        if sub.is_dir():
                            entries.extend(sub.iterdir())
                except OSError:
                    entries = []
                names_lower = {p.name.lower() for p in entries if p.exists()}
                for ac_name, markers in _ANTI_CHEAT_FILE_MARKERS.items():
                    if any(any(m in n for n in names_lower) for m in markers):
                        found.add(ac_name)

        # 2. 进程特征：反作弊服务正在运行
        try:
            import psutil
            running = set()
            for proc in psutil.process_iter(["name"]):
                n = (proc.info.get("name") or "").lower()
                if n:
                    running.add(n)
            for ac_name, markers in _ANTI_CHEAT_PROCESS_MARKERS.items():
                if any(m in running for m in markers):
                    found.add(ac_name)
        except Exception:  # noqa: BLE001 - psutil 不可用/权限不足时仅靠文件特征
            pass

        return sorted(found)

    def _check_anti_cheat_conflicts(self, ac_names: List[str]) -> List[str]:
        """基于识别到的反作弊给出具体冲突提示（无则空清单，不刷无意义警告）。"""
        if not ac_names:
            return []
        kernel_level = {"Riot Vanguard", "Ricochet (使命召唤)"}
        conflicts: List[str] = []
        if kernel_level & set(ac_names):
            conflicts.append(
                f"检测到内核级反作弊（{'、'.join(kernel_level & set(ac_names))}）："
                "虚拟机/调试器/未签名驱动会直接拦截启动，Mod 注入类工具必触发封禁"
            )
        if "EasyAntiCheat (EAC)" in ac_names or "BattlEye" in ac_names:
            conflicts.append(
                "EAC/BattlEye 会扫描注入类 DLL：录屏覆盖层、画质补丁（ReShade）、"
                "Mod 加载器（BepInEx/MelonLoader）可能被误判，联机前请关闭"
            )
        return conflicts

    def _scan_suspicious_processes(self) -> List[str]:
        suspicious: List[str] = []
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").lower()
                if any(k in name for k in _SUSPICIOUS_KEYWORDS):
                    suspicious.append(name)
        except Exception:  # noqa: BLE001
            pass
        return suspicious

    def _check_privacy(self) -> List[str]:
        return ["建议关闭游戏内遥测/诊断数据上传（如可配置）"]

    def _build_recommendations(
        self,
        ac_names: List[str],
        conflicts: List[str],
        suspicious: List[str],
        privacy: List[str],
    ) -> List[str]:
        recs: List[str] = []
        if ac_names:
            recs.append(f"已检测到反作弊组件：{'、'.join(ac_names)}")
        if conflicts:
            recs.append("游玩前关闭可能触发反作弊误判的软件")
        if suspicious:
            recs.append(f"发现可疑进程：{'、'.join(suspicious)}，建议排查")
        recs.extend(privacy)
        recs.append(f"安全扫描频率：{self.scan_frequency}")
        return recs