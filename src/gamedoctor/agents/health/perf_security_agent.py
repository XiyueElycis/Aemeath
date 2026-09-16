"""性能与安全智能体（perf_security）。

合并原 ``performance_analyst`` 与 ``security_agent``：运行体检天然放在一起谈——
CPU/内存/显存瓶颈与反作弊/恶意进程/隐私风险一次巡检覆盖；也支持按 ``op`` 只做单项。

任务分流（``data["op"]`` 显式优先，否则按任务名/能力推断，再否则 ``health`` 全检）：

- ``performance``：仅帧率相关资源采样与瓶颈识别；
- ``security``：仅反作弊双路识别（文件特征 + 运行进程）、恶意进程、隐私；
- ``health``：两项全做。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext
from ...utils.system_info import get_system_info

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

_PERF_CAPS = {"performance", "fps_monitoring", "resource_tracking", "bottleneck_analysis"}
_SECURITY_CAPS = {"security", "anti_cheat", "malware_detection", "privacy_protection"}


@dataclass
class PerformanceSnapshot:
    """性能采样快照。"""

    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    gpu_info: List[Dict[str, Any]] = field(default_factory=list)
    bottlenecks: List[str] = field(default_factory=list)
    optimization_suggestions: List[str] = field(default_factory=list)


@dataclass
class SecuritySnapshot:
    """安全巡检快照。"""

    anti_cheat_detected: bool = False
    anti_cheat_names: List[str] = field(default_factory=list)
    anti_cheat_conflicts: List[str] = field(default_factory=list)
    suspicious_processes: List[str] = field(default_factory=list)
    privacy_issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class PerfSecurityResult:
    """性能与安全体检结果（按 op 只填充对应分块）。"""

    game_name: str
    op: str = "health"
    performance: Optional[PerformanceSnapshot] = None
    security: Optional[SecuritySnapshot] = None


class PerfSecurityAgent(BaseAgent):
    """性能与安全智能体：运行体检（瓶颈 + 反作弊/恶意进程/隐私）。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("perf_security", config)
        self.capabilities = [
            "perf_security",
            "performance", "fps_monitoring", "resource_tracking", "bottleneck_analysis",
            "security", "anti_cheat", "malware_detection", "privacy_protection",
        ]
        self.detailed_analysis = self.config.get("detailed_analysis", True)
        self.optimization_suggestions = self.config.get("optimization_suggestions", True)
        self.anti_cheat_check = self.config.get("anti_cheat_check", True)
        self.scan_frequency = self.config.get("scan_frequency", "daily")

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Perf-Security Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        data = task.data or {}
        game_name = data.get("game_name", "Unknown")
        op = self._infer_op(task, data)

        try:
            snapshot = PerformanceSnapshot()
            security = SecuritySnapshot()
            if op in ("performance", "health"):
                snapshot = self._analyze_performance()
            if op in ("security", "health"):
                security = self._check_security(data.get("game_context"))

            result = PerfSecurityResult(
                game_name=game_name,
                op=op,
                performance=snapshot if op in ("performance", "health") else None,
                security=security if op in ("security", "health") else None,
            )
            if result.performance is not None:
                self.record_metric("cpu_usage", result.performance.cpu_usage)
                self.record_metric("memory_usage", result.performance.memory_usage)
            if result.security is not None:
                self.record_metric("suspicious_process_count",
                                   len(result.security.suspicious_processes))
            return AgentResult(
                success=True,
                data=result,
                message=self._build_message(op, game_name, result),
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Perf-security check failed", e)
            return AgentResult(
                success=False,
                message=f"性能安全体检失败：{e}",
                errors=[str(e)],
            )

    # ------------------------------------------------------------------ #
    # 任务分流
    # ------------------------------------------------------------------ #
    def _infer_op(self, task: AgentTask, data: Dict[str, Any]) -> str:
        """data["op"]/data["operation"] → 任务名关键词 → 能力标签 → 默认 health 全检。"""
        explicit = (data.get("op") or data.get("operation") or "").strip().lower()
        if explicit in ("performance", "perf", "fps", "性能"):
            return "performance"
        if explicit in ("security", "anti_cheat", "安全"):
            return "security"
        if explicit in ("health", "check", "all", "体检", "全部"):
            return "health"

        name = (task.name or "").lower()
        # 直接点名整个智能体（/run perf_security）时不做单项推断，落到全检
        if name not in ("perf_security", "manual_perf_security"):
            if any(k in name for k in ("security", "anti_cheat", "安全")):
                return "security"
            if any(k in name for k in ("perf", "fps", "性能")):
                return "performance"

        caps = {c.lower() for c in (task.required_capabilities or [])}
        if caps & _SECURITY_CAPS and not (caps & _PERF_CAPS):
            return "security"
        if caps & _PERF_CAPS and not (caps & _SECURITY_CAPS):
            return "performance"
        return "health"

    # ------------------------------------------------------------------ #
    # 性能分析（原 performance_analyst 逻辑）
    # ------------------------------------------------------------------ #
    def _analyze_performance(self) -> PerformanceSnapshot:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent
        gpus = get_system_info().get("gpu", [])
        bottlenecks = self._identify_bottlenecks(cpu, mem, gpus)
        suggestions = (self._generate_suggestions(bottlenecks)
                       if self.optimization_suggestions else [])
        return PerformanceSnapshot(
            cpu_usage=cpu,
            memory_usage=mem,
            gpu_info=gpus,
            bottlenecks=bottlenecks,
            optimization_suggestions=suggestions,
        )

    def _identify_bottlenecks(
        self, cpu: float, mem: float, gpus: List[Dict[str, Any]]
    ) -> List[str]:
        bottlenecks: List[str] = []
        if cpu > 85:
            bottlenecks.append("CPU 占用过高，可能存在 CPU 瓶颈")
        if mem > 90:
            bottlenecks.append("内存接近耗尽，建议增加物理内存或关闭后台程序")
        for gpu in gpus:
            vram = gpu.get("memory", 0)
            if vram and vram < 4 * 1024 * 1024 * 1024:  # 小于 4GB
                bottlenecks.append("显存偏小，高画质下可能出现显存瓶颈")
        if not bottlenecks:
            bottlenecks.append("未发现明显瓶颈")
        return bottlenecks

    def _generate_suggestions(self, bottlenecks: List[str]) -> List[str]:
        suggestions: List[str] = []
        for b in bottlenecks:
            if "CPU" in b:
                suggestions.append("降低游戏内物理/阴影等 CPU 密集项")
            if "内存" in b:
                suggestions.append("关闭多余后台进程，或升级内存")
            if "显存" in b:
                suggestions.append("降低纹理质量与分辨率缩放")
        return suggestions or ["当前性能良好，可保持现有设置"]

    # ------------------------------------------------------------------ #
    # 安全巡检（原 security_agent 逻辑）
    # ------------------------------------------------------------------ #
    def _check_security(self, context: GameContext) -> SecuritySnapshot:
        ac_names = self._detect_anti_cheat(context) if self.anti_cheat_check else []
        conflicts = self._check_anti_cheat_conflicts(ac_names)
        suspicious = self._scan_suspicious_processes()
        privacy = self._check_privacy()
        return SecuritySnapshot(
            anti_cheat_detected=bool(ac_names),
            anti_cheat_names=ac_names,
            anti_cheat_conflicts=conflicts,
            suspicious_processes=suspicious,
            privacy_issues=privacy,
            recommendations=self._build_security_recommendations(
                ac_names, conflicts, suspicious, privacy),
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
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").lower()
                if any(k in name for k in _SUSPICIOUS_KEYWORDS):
                    suspicious.append(name)
        except Exception:  # noqa: BLE001
            pass
        return suspicious

    def _check_privacy(self) -> List[str]:
        return ["建议关闭游戏内遥测/诊断数据上传（如可配置）"]

    def _build_security_recommendations(
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

    # ------------------------------------------------------------------ #
    # 汇总话术
    # ------------------------------------------------------------------ #
    def _build_message(self, op: str, game_name: str, result: PerfSecurityResult) -> str:
        parts: List[str] = []
        if result.performance is not None:
            parts.append(f"性能：{len(result.performance.bottlenecks)} 项瓶颈结论")
        if result.security is not None:
            sec = result.security
            parts.append(
                f"安全：识别反作弊 {len(sec.anti_cheat_names)} 个"
                f"、可疑进程 {len(sec.suspicious_processes)} 个")
        scope = {"performance": "性能分析", "security": "安全巡检", "health": "运行体检"}[op]
        return f"{game_name} {scope}完成（{'；'.join(parts)}）"
