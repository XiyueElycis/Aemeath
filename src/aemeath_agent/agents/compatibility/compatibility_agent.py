"""游戏兼容性检测智能体。

检测游戏与系统、硬件的兼容性，提供兼容性报告和优化建议。

判定原则（2026-09 三省六部采集链路重构）：
- 需求档位按**指纹引擎**选择（unreal/unity/godot/renpy/java），硬编码表仅兜底；
- 硬件信息优先取 ``task.context``（GameContext 指纹字段），其次 system_info；
- 任何"未知"（型号解析不出 / 显存 None / 版本缺失）一律**不判不合格**——
  漏报优于误报，绝不让 Win11 + RTX 4070 的机器凭空吃 critical；
- CPU/GPU 只在同厂商、同命名体系内做型号比较，跨体系交给显存等硬指标。
"""

from __future__ import annotations

import logging
import re
import psutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..base_agent import BaseAgent, AgentTask, AgentResult
from ...models import GameContext

# 不同引擎的典型最低配置档位（vram/ram/storage 单位 MB；dx=None 表示不强制）。
# 档位依据各引擎官方最低规格的常见区间，取较宽的下限以降低误报。
_REQUIREMENTS_BY_ENGINE: Dict[str, Dict[str, Any]] = {
    "renpy":  {"cpu": "i3-3220", "gpu": "Intel HD 4000", "vram": 512,
               "ram": 4096, "storage": 2048, "dx": None, "os_build": 7601},
    "java":   {"cpu": "i3-3220", "gpu": "GTX 750", "vram": 1024,
               "ram": 4096, "storage": 4096, "dx": None, "os_build": 7601},
    "godot":  {"cpu": "i3-4000", "gpu": "GTX 750", "vram": 1024,
               "ram": 4096, "storage": 4096, "dx": 11, "os_build": 7601},
    "unity":  {"cpu": "i5-2500", "gpu": "GTX 750 Ti", "vram": 2048,
               "ram": 8192, "storage": 20480, "dx": 11, "os_build": 10240},
    "unreal": {"cpu": "i5-7500", "gpu": "GTX 970", "vram": 4096,
               "ram": 16384, "storage": 51200, "dx": 11, "os_build": 10240},
}

# 识别不出引擎时的保守兜底档（沿用旧硬编码值）
_DEFAULT_REQUIREMENTS: Dict[str, Any] = {
    "cpu": "i5-7500", "gpu": "GTX 970", "vram": 4096,
    "ram": 8192, "storage": 50000, "dx": 11, "os_build": 10240,
}


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


# --------------------------------------------------------------------------- #
# 型号解析（模块级纯函数，便于单测）
# --------------------------------------------------------------------------- #

def _parse_cpu_tier(name: str) -> Optional[Tuple[str, int, int]]:
    """把 CPU 型号解析为 ``(厂商, 系列档, 代数)``；解析不出返回 None。

    - Intel：``i5-7500`` → ("intel", 5, 7)；3 位型号（i5-750）为 1 代酷睿；
      4 位型号首位为代数（4590→4/9700→9），5 位取前两位（10400→10/12700→12）；
    - AMD：``Ryzen 5 3600`` → ("amd", 5, 3)；型号首位即 Zen 代数。
    """
    low = (name or "").lower()
    m = re.search(r"\bi([3579])[\s\-]?(\d{3,5})[a-z]{0,2}\b", low)
    if m:
        tier = int(m.group(1))
        model = m.group(2)
        if len(model) == 3:
            gen = 1
        elif len(model) == 4:
            gen = int(model[0])
        else:
            gen = int(model[:2])
        return "intel", tier, gen
    m = re.search(r"ryzen\s*([3579])\s*(\d{4})[a-z0-9]*", low)
    if m:
        return "amd", int(m.group(1)), int(m.group(2)[0])
    return None


def _parse_gpu_tier(name: str) -> Optional[Tuple[str, int, int]]:
    """把 GPU 型号解析为 ``(体系, 前缀权重, 型号数)``；解析不出返回 None。

    同体系内数字越大越强；RTX 前缀整体高于 GTX。核显自成体系，不与独显混比。
    """
    low = (name or "").lower()
    m = re.search(r"\b(rtx|gtx|gt)\s*(\d{3,4})\b", low)
    if m:
        weight = {"rtx": 2, "gtx": 1, "gt": 0}[m.group(1)]
        return "nvidia", weight, int(m.group(2))
    m = re.search(r"\brx\s*(\d{3,4})\b", low)
    if m:
        return "amd", 1, int(m.group(1))
    if "iris" in low:
        m2 = re.search(r"iris\s*(?:xe|plus|pro)?\s*(\d{3,4})?", low)
        return "intel_igpu", 1, int(m2.group(1)) if (m2 and m2.group(1)) else 9000
    m = re.search(r"(?:hd|uhd)\s*graphics\s*(\d{3,4})", low)
    if m:
        return "intel_igpu", 0, int(m.group(1))
    return None


def _leading_number(text: Any) -> Optional[float]:
    """取字符串开头的数字（``"12 Ultimate"`` → 12.0）；无数字返回 None。"""
    if text is None:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)", str(text))
    return float(m.group(1)) if m else None


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
                "description": "引擎档位要求较高（UE 类），低配机器风险大",
                "engines": ["unreal"],
                "thresholds": {"cpu": "i7-4770", "gpu": "GTX 970"}
            },
            "dx12_requirement": {
                "description": "新游戏可能需要 DirectX 12",
                "engines": ["unreal"],
                "min_version": 12
            }
        }

    async def execute(self, task: AgentTask) -> AgentResult:
        """执行兼容性检测任务。"""
        try:
            system_info = get_system_info_safe()
            gpu_info = system_info.get("gpu", [])

            data = task.data or {}
            game_name = data.get("game_name") or "Unknown"

            # 指纹来源优先级：task.context（治理链路注入）→ data.fingerprint（跨边界 dict）
            gc: Optional[GameContext] = getattr(task, "context", None)
            fingerprint = data.get("fingerprint") or {}
            engine = (getattr(gc, "engine", None) if gc else None) or fingerprint.get("engine")

            install_path = ""
            if gc is not None and gc.install_path:
                install_path = str(gc.install_path)
            install_path = install_path or fingerprint.get("game_dir") or ""

            compatibility_result = self._check_compatibility(
                game_name, system_info, gpu_info,
                game_context=gc, engine=engine, install_path=install_path,
                fingerprint=fingerprint,
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

    def _requirements_for(self, engine: Optional[str]) -> Dict[str, Any]:
        """按引擎选需求档位；未知引擎用保守兜底档。"""
        if engine and engine in _REQUIREMENTS_BY_ENGINE:
            return dict(_REQUIREMENTS_BY_ENGINE[engine])
        return dict(_DEFAULT_REQUIREMENTS)

    def _check_compatibility(
        self,
        game_name: str,
        system_info: Dict[str, Any],
        gpu_info: List[Dict[str, Any]],
        game_context: GameContext = None,
        engine: Optional[str] = None,
        install_path: str = "",
        fingerprint: Optional[Dict[str, Any]] = None,
    ) -> CompatibilityResult:
        """执行详细的兼容性检查。"""
        fingerprint = fingerprint or {}
        requirements = self._requirements_for(engine)
        issues: List[str] = []
        warnings: List[str] = []
        recommendations: List[str] = []

        # 解析当前机器的真实硬件（指纹优先，system_info 兜底）
        current_cpu = system_info.get("cpu", {}).get("name", "")
        current_gpu = (
            (getattr(game_context, "gpu_name", None) if game_context else None)
            or fingerprint.get("gpu")
            or (gpu_info[0].get("name", "") if gpu_info else "")
        )
        # 显存：指纹 None 表示未知，不能用 system_info 的 0 顶替（0 会造成误判）
        vram_mb: Optional[int] = None
        if game_context is not None and getattr(game_context, "gpu_vram_mb", None) is not None:
            vram_mb = game_context.gpu_vram_mb
        elif fingerprint.get("gpu_vram_mb") is not None:
            vram_mb = fingerprint.get("gpu_vram_mb")
        elif gpu_info:
            mem = gpu_info[0].get("memory") or 0
            vram_mb = mem or None

        # 1. 检查操作系统兼容性
        os_compatibility = self._check_os_compatibility(system_info, requirements)
        issues.extend(os_compatibility["issues"])
        warnings.extend(os_compatibility["warnings"])

        # 2. 检查硬件要求
        hardware = self._check_hardware_requirements(
            requirements, current_cpu, current_gpu, vram_mb,
            system_info.get("memory", {}).get("total", 0), install_path,
        )
        issues.extend(hardware["issues"])
        recommendations.extend(hardware["recommendations"])

        # 3. 检查运行时依赖
        runtime_compatibility = self._check_runtime_compatibility(system_info, requirements)
        issues.extend(runtime_compatibility["issues"])

        # 4. 检查已知问题（按引擎档位，不再按游戏名关键词猜）
        known_issues = self._check_known_issues(system_info, engine)
        issues.extend(known_issues["critical"])
        warnings.extend(known_issues["warnings"])

        # 计算总体评分
        overall_score = self._calculate_compatibility_score(issues, warnings)

        return CompatibilityResult(
            overall_score=overall_score,
            critical_issues=issues,
            warnings=warnings,
            recommendations=recommendations,
            system_requirements=requirements,
            hardware_info={
                "engine": engine,
                "cpu": current_cpu,
                "gpu": current_gpu or "Unknown",
                "gpu_vram_mb": vram_mb,
                "ram": system_info.get("memory", {}).get("total", 0),
                "os_build": system_info.get("os", {}).get("build", ""),
                "storage": self._install_drive_total(install_path),
            },
            known_issues=known_issues["issues"]
        )

    # ------------------------------------------------------------------ #
    def _check_os_compatibility(
        self, system_info: Dict[str, Any], requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        """检查操作系统兼容性（以真实 build 号判定，不再信遗留 6.3）。"""
        os_info = system_info.get("os", {})
        os_name = os_info.get("name", "Unknown")
        build_str = str(os_info.get("build") or "")

        issues: List[str] = []
        warnings: List[str] = []

        if "Windows" in os_name:
            min_build = int(requirements.get("os_build") or 0)
            build_num = _leading_number(build_str)
            if min_build and build_num is not None and build_num < min_build:
                issues.append(
                    f"游戏档位要求 build ≥ {min_build}（约 "
                    f"{'Windows 10' if min_build >= 10240 else 'Windows 7 SP1'}），"
                    f"当前系统：{os_name} build {build_str}"
                )
            elif build_num is None:
                # 取不到 build 不表态，只提示
                warnings.append("未能读取 Windows build 号，OS 兼容性未判定")
        elif "macOS" in os_name or "Darwin" in os_name:
            warnings.append("macOS 存在版本兼容性风险时需进一步确认")

        return {"issues": issues, "warnings": warnings}

    def _check_hardware_requirements(
        self,
        requirements: Dict[str, Any],
        cpu: str,
        gpu: str,
        gpu_vram_mb: Optional[int],
        ram_bytes: int,
        install_path: str,
    ) -> Dict[str, Any]:
        """检查硬件要求（CPU/GPU/内存/安装盘空间）。"""
        issues: List[str] = []
        recommendations: List[str] = []

        # CPU：解析不出型号不判不合格
        if not self._is_cpu_compatible(cpu, requirements["cpu"]):
            issues.append(f"CPU 可能不满足要求: {cpu or '未知'}（参考档位 {requirements['cpu']}）")

        # GPU：第 4 参是"显存需求"，不再错传 storage；None=未知/未要求 → 不表态
        if not self._is_gpu_compatible(
            gpu, gpu_vram_mb, requirements["gpu"], requirements.get("vram")
        ):
            detail = f"，显存 {gpu_vram_mb}MB < {requirements.get('vram')}MB" \
                if gpu_vram_mb is not None and requirements.get("vram") else ""
            issues.append(f"GPU 可能不满足要求: {gpu or '未知'}{detail}")

        # 内存（需求单位 MB，系统值为字节）
        ram_req_mb = int(requirements.get("ram") or 0)
        if ram_req_mb and ram_bytes < ram_req_mb * 1024 * 1024:
            issues.append(f"内存不足: {ram_bytes / 1024 / 1024 / 1024:.1f}GB"
                          f" < {ram_req_mb / 1024:.0f}GB")

        # 安装盘剩余空间（知道安装目录才判，不猜 C 盘）
        storage_req_mb = int(requirements.get("storage") or 0)
        if storage_req_mb and install_path:
            try:
                free_mb = psutil.disk_usage(install_path).free / (1024 * 1024)
                if free_mb < storage_req_mb:
                    issues.append(f"安装盘剩余空间不足: {free_mb / 1024:.1f}GB"
                                  f" < 需求 {storage_req_mb / 1024:.0f}GB")
            except OSError:
                pass

        if issues:
            recommendations.append("可尝试降低画质/分辨率，关闭后台占用后复测")

        return {"issues": issues, "recommendations": recommendations}

    @staticmethod
    def _install_drive_total(install_path: str) -> int:
        if not install_path:
            return 0
        try:
            return psutil.disk_usage(install_path).total
        except OSError:
            return 0

    def _check_runtime_compatibility(
        self, system_info: Dict[str, Any], requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        """检查运行时兼容性（DirectX / .NET；版本未知不表态）。"""
        issues: List[str] = []

        directx_version = system_info.get("directx", {}).get("version", "")
        if directx_version and directx_version != "Unknown":
            dx_num = _leading_number(directx_version)
            dx_req = requirements.get("dx")
            if dx_num is not None and dx_req is not None and dx_num < dx_req:
                issues.append(f"DirectX 版本过低: {directx_version}（档位要求 {dx_req}）")

        # .NET Framework 仅在系统返回了真实版本时才比较
        net_version = system_info.get("net_framework", {}).get("version", "")
        net_num = _leading_number(net_version)
        if net_num is not None and net_num < 4.7:
            issues.append(f".NET Framework 版本过低: {net_version}")

        return {"issues": issues}

    def _check_known_issues(
        self,
        system_info: Dict[str, Any],
        engine: Optional[str],
    ) -> Dict[str, Any]:
        """检查已知问题（按引擎命中，替代旧的游戏名关键词猜测）。"""
        critical: List[str] = []
        warnings: List[str] = []
        issues: List[Dict[str, Any]] = []

        for issue_key, issue_data in self.known_issues_db.items():
            engines = issue_data.get("engines", [])
            if engine not in engines:
                continue
            if issue_key == "high_system_requirements":
                critical.append(issue_data["description"])
                issues.append({
                    "type": "system_requirement",
                    "severity": "critical",
                    "description": issue_data["description"],
                })
            elif issue_key == "dx12_requirement":
                min_version = issue_data.get("min_version")
                dx_num = _leading_number(
                    system_info.get("directx", {}).get("version", "")
                )
                if min_version is not None and dx_num is not None and dx_num < min_version:
                    warnings.append("该引擎档位的新游戏可能需要 DirectX 12")
                    issues.append({
                        "type": "dx12_requirement",
                        "severity": "warning",
                        "description": "该引擎档位的新游戏可能需要 DirectX 12",
                    })

        return {"critical": critical, "warnings": warnings, "issues": issues}

    def _calculate_compatibility_score(self, critical_issues: List[str], warnings: List[str]) -> float:
        """计算兼容性评分。"""
        score = 100
        score -= len(critical_issues) * 20   # 每个严重问题扣 20 分
        score -= len(warnings) * 5           # 每个警告扣 5 分
        return max(0, score)

    def _is_cpu_compatible(self, current_cpu: str, required_cpu: str) -> bool:
        """CPU 档位比较。解析不出任一方型号 → True（漏报优于误报）。"""
        cur = _parse_cpu_tier(current_cpu)
        req = _parse_cpu_tier(required_cpu)
        if cur is None or req is None:
            return True
        if cur[0] != req[0]:
            return True  # 跨厂商不硬比
        if cur[1] != req[1]:
            return cur[1] > req[1]
        return cur[2] >= req[2]

    def _is_gpu_compatible(
        self,
        current_gpu: str,
        current_vram_mb: Optional[int],
        required_gpu: str,
        required_vram_mb: Optional[int],
    ) -> bool:
        """GPU 档位 + 显存比较。None 表示未知/未要求，对应项不参与判定。"""
        if required_vram_mb is not None and current_vram_mb is not None:
            if current_vram_mb < required_vram_mb:
                return False
        cur = _parse_gpu_tier(current_gpu)
        req = _parse_gpu_tier(required_gpu)
        if cur is None or req is None:
            return True
        if cur[0] != req[0]:
            return True  # 跨体系（独显/核显/不同厂商）不硬比，显存已兜底
        if cur[1] != req[1]:
            return cur[1] > req[1]
        return cur[2] >= req[2]

    def get_compatibility_report(self, game_name: str, engine: Optional[str] = None) -> Dict[str, Any]:
        """获取兼容性报告。"""
        system_info = get_system_info_safe()
        gpu_info = system_info.get("gpu", [])

        result = self._check_compatibility(
            game_name, system_info, gpu_info, engine=engine
        )

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


def get_system_info_safe() -> Dict[str, Any]:
    """局部导入避免模块加载期循环依赖；采集失败返回空骨架而非抛异常。"""
    try:
        from ...utils.system_info import get_system_info
        return get_system_info()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("系统信息采集失败，按空值判定", exc_info=True)
        return {"cpu": {}, "gpu": [], "memory": {}, "os": {}, "directx": {},
                "net_framework": {}}
