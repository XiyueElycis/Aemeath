"""网络连接专家智能体。

网络连接优化：Ping 测试与路由优化、端口配置、NAT 检测、服务器推荐。
"""

from __future__ import annotations

import logging
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

    # 常见联机游戏服务端口（占位，可按游戏维护）
    _COMMON_PORTS = {"udp": [3074, 27015, 7777], "tcp": [80, 443]}

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("network_expert", config)
        self.capabilities = [
            "network", "ping_test", "port_config", "nat_detection", "server_recommendation",
        ]
        self.route_optimization = self.config.get("route_optimization", True)
        self.server_recommendation = self.config.get("server_recommendation", True)

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Network Expert Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            data = task.data or {}
            game_name = data.get("game_name", "Unknown")
            target_host = data.get("host", "8.8.8.8")

            latency = self._ping(target_host)
            nat = self._detect_nat()
            ports = self._recommended_ports(game_name)
            servers = self._recommend_servers(game_name) if self.server_recommendation else []

            issues = self._identify_issues(latency, nat)
            result = NetworkResult(
                game_name=game_name,
                latency_ms=latency,
                nat_type=nat,
                recommended_ports=ports,
                recommended_servers=servers,
                issues=issues,
                recommendations=self._build_recommendations(latency, nat),
            )

            self.record_metric("latency_ms", latency)
            return AgentResult(
                success=True,
                data=result,
                message=f"Network analysis completed for {game_name}",
            )
        except Exception as e:  # noqa: BLE001
            self.log_error("Network analysis failed", e)
            return AgentResult(
                success=False,
                message=f"Network analysis failed: {e}",
                errors=[str(e)],
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

    def _detect_nat(self) -> str:
        # TODO: 通过 STUN 等协议真实检测 NAT 类型
        return "unknown"

    def _recommended_ports(self, game_name: str) -> List[int]:
        ports = list(self._COMMON_PORTS["udp"])
        ports.extend(self._COMMON_PORTS["tcp"])
        return sorted(set(ports))

    def _recommend_servers(self, game_name: str) -> List[str]:
        # TODO: 按延迟实测推荐最近服
        return ["自动选择（最低延迟）"]

    def _identify_issues(self, latency: float | None, nat: str) -> List[str]:
        issues: List[str] = []
        if latency is None:
            issues.append("无法建立到目标主机的连接")
        elif latency > 150:
            issues.append(f"延迟偏高（{latency}ms），可能影响联机体验")
        if nat in ("strict",):
            issues.append("NAT 类型为严格，可能无法联机")
        return issues

    def _build_recommendations(self, latency: float | None, nat: str) -> List[str]:
        recs: List[str] = []
        if latency is None:
            recs.append("请检查网络连通性与防火墙")
        else:
            recs.append(f"当前延迟 {latency}ms")
        if self.route_optimization:
            recs.append("已开启路由优化建议")
        recs.append("如有联机问题，建议确认端口转发/UPnP 是否开启")
        return recs