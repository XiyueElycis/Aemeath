"""网络连接专家智能体。

网络连接优化：Ping 测试与路由优化、端口配置、NAT 检测、服务器推荐。
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...models import GameContext


@dataclass
class NetworkResult:
    """网络连接分析与优化结果。"""

    game_name: str
    latency_ms: float | None = None
    nat_type: str = "unknown"           # open / moderate / strict / unknown
    recommended_ports: List[int] = field(default_factory=list)
    recommended_servers: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class NetworkExpertAgent(BaseAgent):
    """网络连接专家智能体。"""

    # 默认探测主机：仅验证本机公网连通性，不代表游戏服务器可达
    _DEFAULT_PROBE_HOST = "8.8.8.8"

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("network_expert", config)
        self.capabilities = [
            "network", "ping_test", "port_config", "nat_detection", "server_recommendation",
        ]
        self.route_optimization = self.config.get("route_optimization", True)
        self.server_recommendation = self.config.get("server_recommendation", True)
        # 游戏端口表必须显式配置：{游戏名: {"udp": [...], "tcp": [...]}}，
        # 不再对所有游戏返回同一张写死的「常见端口」表冒充专属建议。
        self.game_ports: Dict[str, Dict[str, List[int]]] = self.config.get("game_ports", {})

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Network Expert Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        data = task.data or {}
        game_name = data.get("game_name", "Unknown")
        target_host = data.get("host") or self._DEFAULT_PROBE_HOST

        # 唯一有真实探测支撑的项目：TCP 连接测时（默认主机只代表公网连通性）
        latency = self._ping(target_host)
        ports = self._recommended_ports(game_name, data)
        issues = self._identify_issues(latency, "unknown")

        # NAT（STUN）、就近服务器推荐尚未接入；端口表未配置时端口建议也不成立
        unverified = ["NAT 类型检测（STUN 未接入）", "就近服务器推荐"]
        if target_host == self._DEFAULT_PROBE_HOST:
            unverified.append("游戏服务器连通性（当前仅探测通用公网主机）")
        if not ports:
            unverified.append("游戏专用端口配置（端口表未配置该游戏）")

        result = NetworkResult(
            game_name=game_name,
            latency_ms=latency,
            nat_type="unknown",
            recommended_ports=ports,
            recommended_servers=[],
            issues=issues,
            recommendations=self._build_recommendations(latency, bool(ports)),
        )

        self.record_metric("latency_ms", latency)
        return AgentResult(
            success=True,
            data=result,
            message=(
                f"Network analysis partial for {game_name}：仅完成到 {target_host} 的"
                f"TCP 测时；NAT/服务器推荐等能力尚未接入，未验证项不可作为诊断结论"
            ),
            unverified=unverified,
        )

    def _ping(self, host: str, port: int = 443, timeout: float = 2.0) -> float | None:
        """TCP 连接测时（简化 ping）。"""
        try:
            start = time.perf_counter()
            with socket.create_connection((host, port), timeout=timeout):
                pass
            return round((time.perf_counter() - start) * 1000, 1)
        except Exception:  # noqa: BLE001
            return None

    def _recommended_ports(self, game_name: str, data: Dict[str, Any] | None = None) -> List[int]:
        """端口表来源：任务 data.ports → 配置 game_ports[游戏名]；均无则返回空。"""
        table = (data or {}).get("ports")
        if isinstance(table, dict):
            entry = table
        else:
            entry = self.game_ports.get(game_name) or {}
        if not isinstance(entry, dict):
            return []
        ports = list(entry.get("udp", [])) + list(entry.get("tcp", []))
        return sorted({int(p) for p in ports if isinstance(p, int)})

    def _identify_issues(self, latency: float | None, nat: str) -> List[str]:
        issues: List[str] = []
        if latency is None:
            issues.append("无法建立到目标主机的连接")
        elif latency > 150:
            issues.append(f"延迟偏高（{latency}ms），可能影响联机体验")
        # NAT 真实检测未接入，不得据 "unknown" 产出严格 NAT 结论
        return issues

    def _build_recommendations(self, latency: float | None, ports_configured: bool) -> List[str]:
        # 只给有实测依据的建议；删除「已开启路由优化」等假声明
        recs: List[str] = []
        if latency is None:
            recs.append("请检查网络连通性与防火墙")
        else:
            recs.append(f"当前到探测主机的延迟 {latency}ms")
        if ports_configured:
            recs.append("如有联机问题，建议确认端口转发/UPnP 是否开启")
        return recs