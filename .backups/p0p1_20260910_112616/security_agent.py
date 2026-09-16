"""游戏安全防护智能体。

安全防护：反作弊兼容性检查、恶意软件检测、隐私保护、安全更新提醒。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext

# 常见反作弊（EAC / BattlEye / VAC）与已知冲突场景的关键字
_ANTI_CHEAT_KEYWORDS = ["easyanticheat", "battleye", "vac", "ricochet"]

# 高风险进程关键字（占位，实际可接入安全扫描库）
_SUSPICIOUS_KEYWORDS = ["keylogger", "injector", "cheatengine"]


@dataclass
class SecurityResult:
    """安全防护结果。"""

    game_name: str
    anti_cheat_detected: bool = False
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

            ac_detected = self._detect_anti_cheat(context) if self.anti_cheat_check else False
            conflicts = self._check_anti_cheat_conflicts(game_name)
            suspicious = self._scan_suspicious_processes()
            privacy = self._check_privacy()

            result = SecurityResult(
                game_name=game_name,
                anti_cheat_detected=ac_detected,
                anti_cheat_conflicts=conflicts,
                suspicious_processes=suspicious,
                privacy_issues=privacy,
                recommendations=self._build_recommendations(conflicts, suspicious, privacy),
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

    def _detect_anti_cheat(self, context: GameContext) -> bool:
        # TODO: 通过目录/文件特征识别反作弊组件
        if context and context.install_path:
            name = context.install_path.name.lower()
            return any(k in name for k in _ANTI_CHEAT_KEYWORDS)
        return False

    def _check_anti_cheat_conflicts(self, game_name: str) -> List[str]:
        conflicts: List[str] = []
        # 反作弊常与某些软件冲突（占位逻辑）
        conflicts.append("部分录屏/DLL 注入类工具可能被反作弊误判")
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
        conflicts: List[str],
        suspicious: List[str],
        privacy: List[str],
    ) -> List[str]:
        recs: List[str] = []
        if conflicts:
            recs.append("游玩前关闭可能触发反作弊误判的软件")
        if suspicious:
            recs.append(f"发现可疑进程：{'、'.join(suspicious)}，建议排查")
        recs.extend(privacy)
        recs.append(f"安全扫描频率：{self.scan_frequency}")
        return recs