"""游戏兼容性检测智能体。

检测游戏与系统、硬件的兼容性，提供兼容性报告和优化建议。
"""

from __future__ import annotations

import logging
import platform
import psutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..base_agent import BaseAgent, AgentTask, AgentResult
from ...models import GameContext, TechStackFingerprint
from ...utils.system_info import get_system_info


@dataclass
class CompatibilityResult:
    """兼容性检测结果。"""
    overall_score: float  # 0-100 的兼容性评分
    critical_issues: List[str]
    warnings: List[str]
    recommendations: List[str]
    system_requirements: Dict[str, Any]
    hardware_info: Dict[str, Any]
    known_issues: List[Dict[str, Any]]


class CompatibilityAgent(BaseAgent):
    """兼容性检测智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("compatibility", config)
        self.capabilities = ["compatibility", "compatibility_check", "system_analysis", "hardware_detection"]
        self.known_issues_db = {}  # 应该从数据库加载
        self._requirements_cache = {}

    def _do_initialize(self) -> None:
        """初始化智能体。"""
        self.logger.info("Initializing Compatibility Agent")
        # 加载已知问题数据库
        self._load_known_issues()

    def _load_known_issues(self) -> None:
        """加载已知问题数据库。"""
        # 这里应该从数据库或配置文件加载
        # 示例数据
        self.known_issues_db = {
            "high_system_requirements": {
                "description": "系统要求过高",
                "affects": ["AAA", "OpenWorld"],
                "thresholds": {"cpu": "i7-4770", "gpu": "GTX 970"}
            },
            "dx12_requirement": {
                "description": "需要 DirectX 12",
                "affects": ["ModernGames"],
                "min_version": 12
            }
        }

    async def execute(self, task: AgentTask) -> AgentResult:
        """执行兼容性检测任务。"""
        try:
            # 获取系统信息（GPU 列表已包含在 system_info["gpu"] 中）
            system_info = get_system_info()
            gpu_info = system_info.get("gpu", [])

            # 获取游戏上下文
            game_context = (task.data or {}).get("game_context")
            game_name = (task.data or {}).get("game_name") or "Unknown"

            # 执行兼容性检查
            compatibility_result = self._check_compatibility(
                game_name, system_info, gpu_info, game_context
            )

            # 记录指标
            self.record_metric("compatibility_score", compatibility_result.overall_score)
            self.record_metric("critical_issues_count", len(compatibility_result.critical_issues))
            self.record_metric("warnings_count", len(compatibility_result.warnings))

            return AgentResult(
                success=True,
                data=compatibility_result,
                message=f"Compatibility check completed for {game_name}"
            )

        except Exception as e:
            self.log_error("Compatibility check failed", e)
            return AgentResult(
                success=False,
                message=f"Compatibility check failed: {str(e)}",
                errors=[str(e)]
            )

    def _check_compatibility(
        self,
        game_name: str,
        system_info: Dict[str, Any],
        gpu_info: Dict[str, Any],
        game_context: GameContext = None
    ) -> CompatibilityResult:
        """执行详细的兼容性检查。"""
        issues = []
        warnings = []
        recommendations = []

        # 1. 检查操作系统兼容性
        os_compatibility = self._check_os_compatibility(game_name, system_info)
        if not os_compatibility["compatible"]:
            issues.extend(os_compatibility["issues"])
            warnings.extend(os_compatibility["warnings"])

        # 2. 检查硬件要求
        hardware_compatibility = self._check_hardware_requirements(game_name, system_info, gpu_info)
        if not hardware_compatibility["compatible"]:
            issues.extend(hardware_compatibility["issues"])
            recommendations.extend(hardware_compatibility["recommendations"])

        # 3. 检查运行时依赖
        runtime_compatibility = self._check_runtime_compatibility(game_name, system_info)
        if not runtime_compatibility["compatible"]:
            issues.extend(runtime_compatibility["issues"])

        # 4. 检查已知问题
        known_issues = self._check_known_issues(game_name, system_info, gpu_info)
        issues.extend(known_issues["critical"])
        warnings.extend(known_issues["warnings"])

        # 计算总体评分
        overall_score = self._calculate_compatibility_score(issues, warnings)

        return CompatibilityResult(
            overall_score=overall_score,
            critical_issues=issues,
            warnings=warnings,
            recommendations=recommendations,
            system_requirements=hardware_compatibility["requirements"],
            hardware_info={
                "cpu": system_info.get("cpu", {}).get("name", ""),
                "gpu": gpu_info[0].get("name", "") if gpu_info else "Unknown",
                "ram": system_info.get("memory", {}).get("total", 0),
                "storage": psutil.disk_usage('/').total if hasattr(psutil, 'disk_usage') else 0
            },
            known_issues=known_issues["issues"]
        )

    def _check_os_compatibility(self, game_name: str, system_info: Dict[str, Any]) -> Dict[str, Any]:
        """检查操作系统兼容性。"""
        os_info = system_info.get("os", {})
        os_name = os_info.get("name", "Unknown")
        os_version = os_info.get("version", "")

        issues = []
        warnings = []
        compatible = True

        # 简化的检查逻辑：对比主版本号，安全解析字符串，避免 float() 抛异常
        if "Windows" in os_name:
            major = self._version_to_float(os_version)
            if major is not None and major < 10:
                issues.append(f"游戏要求 Windows 10 或更高版本，当前系统: {os_name}")
                compatible = False
        elif "macOS" in os_name:
            warnings.append("macOS 存在版本兼容性风险时需进一步确认")

        return {
            "compatible": compatible,
            "issues": issues,
            "warnings": warnings
        }

    def _check_hardware_requirements(
        self,
        game_name: str,
        system_info: Dict[str, Any],
        gpu_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """检查硬件要求。"""
        requirements = {
            "cpu": "i5-7500",
            "gpu": "GTX 970",
            "ram": 8192,  # MB
            "storage": 50000  # MB
        }

        issues = []
        recommendations = []
        compatible = True

        # 检查 CPU
        cpu = system_info.get("cpu", {}).get("name", "")
        if not self._is_cpu_compatible(cpu, requirements["cpu"]):
            issues.append(f"CPU 可能不满足要求: {cpu}")
            compatible = False

        # 检查 GPU（gpu_info 为列表，取第一块 GPU）
        gpu = gpu_info[0].get("name", "") if gpu_info else ""
        gpu_memory = gpu_info[0].get("memory", 0) if gpu_info else 0
        if not self._is_gpu_compatible(gpu, gpu_memory, requirements["gpu"], requirements["storage"]):
            issues.append(f"GPU 可能不满足要求: {gpu}")
            compatible = False

        # 检查内存（requirements["ram"] 单位为 MB，系统内存为字节）
        memory = system_info.get("memory", {}).get("total", 0)
        if memory < requirements["ram"] * 1024 * 1024:
            issues.append(f"内存不足: {memory/1024/1024:.1f}GB < {requirements['ram']/1024}GB")
            compatible = False

        return {
            "compatible": compatible,
            "issues": issues,
            "recommendations": recommendations,
            "requirements": requirements
        }

    @staticmethod
    def _version_to_float(version: str) -> Optional[float]:
        """把版本字符串解析为浮点数，无法解析时返回 None。"""
        try:
            return float(str(version).split(".")[0])
        except (ValueError, TypeError, AttributeError):
            return None

    def _check_runtime_compatibility(self, game_name: str, system_info: Dict[str, Any]) -> Dict[str, Any]:
        """检查运行时兼容性。"""
        issues = []
        compatible = True

        # 检查 DirectX
        directx_version = system_info.get("directx", {}).get("version", "")
        dx_num = self._version_to_float(directx_version)
        if dx_num is not None and dx_num < 11:
            issues.append(f"DirectX 版本过低: {directx_version}")
            compatible = False

        # 检查 .NET Framework
        net_version = system_info.get("net_framework", {}).get("version", "")
        net_num = self._version_to_float(net_version)
        if net_num is not None and net_num < 4.7:
            issues.append(f".NET Framework 版本过低: {net_version}")
            compatible = False

        return {
            "compatible": compatible,
            "issues": issues
        }

    def _check_known_issues(
        self,
        game_name: str,
        system_info: Dict[str, Any],
        gpu_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """检查已知问题。"""
        critical = []
        warnings = []
        issues = []

        for issue_key, issue_data in self.known_issues_db.items():
            if issue_key == "high_system_requirements":
                # 检查是否是要求高的游戏类型
                if self._is_high_requirement_game(game_name):
                    critical.append(issue_data["description"])
                    issues.append({
                        "type": "system_requirement",
                        "severity": "critical",
                        "description": issue_data["description"]
                    })

            elif issue_key == "dx12_requirement":
                # 检查 DirectX 12 要求
                min_version = issue_data.get("min_version")
                dx_num = self._version_to_float(
                    system_info.get("directx", {}).get("version", "")
                )
                if min_version is not None and dx_num is not None and dx_num < min_version:
                    warnings.append("游戏需要 DirectX 12")
                    issues.append({
                        "type": "dx12_requirement",
                        "severity": "warning",
                        "description": "游戏需要 DirectX 12"
                    })

        return {
            "critical": critical,
            "warnings": warnings,
            "issues": issues
        }

    def _calculate_compatibility_score(self, critical_issues: List[str], warnings: List[str]) -> float:
        """计算兼容性评分。"""
        score = 100

        # 每个严重问题扣 20 分
        score -= len(critical_issues) * 20

        # 每个警告扣 5 分
        score -= len(warnings) * 5

        return max(0, score)

    def _is_cpu_compatible(self, current_cpu: str, required_cpu: str) -> bool:
        """检查 CPU 兼容性。"""
        # 简化的实现，实际应该使用更精确的 CPU 性能对比
        return True  # 简化处理

    def _is_gpu_compatible(self, current_gpu: str, current_vram: int, required_gpu: str, required_vram: int) -> bool:
        """检查 GPU 兼容性。"""
        # 简化的实现
        if current_vram < required_vram:
            return False
        return True  # 简化处理

    def _is_high_requirement_game(self, game_name: str) -> bool:
        """判断是否是要求高的游戏。"""
        # 简化的实现
        return any(keyword in game_name.lower() for keyword in ["aaa", "open world", "rpg"])

    def get_compatibility_report(self, game_name: str) -> Dict[str, Any]:
        """获取兼容性报告。"""
        system_info = get_system_info()
        gpu_info = system_info.get("gpu", [])

        result = self._check_compatibility(game_name, system_info, gpu_info)

        return {
            "game": game_name,
            "compatibility_score": result.overall_score,
            "summary": self._generate_summary(result),
            "details": {
                "critical_issues": result.critical_issues,
                "warnings": result.warnings,
                "recommendations": result.recommendations
            }
        }

    def _generate_summary(self, result: CompatibilityResult) -> str:
        """生成兼容性摘要。"""
        if result.overall_score >= 80:
            return "高兼容性，应该可以正常运行"
        elif result.overall_score >= 60:
            return "中等兼容性，可能需要优化"
        elif result.overall_score >= 40:
            return "低兼容性，可能有严重问题"
        else:
            return "不兼容，不建议安装"