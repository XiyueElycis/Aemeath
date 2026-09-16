"""ApprovalGate 授权门测试：批准/拒绝/超时/并行独立/无忙等。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from gamedoctor.sandbox.gate import ApprovalGate, ApprovalRequest, get_gate, reset_gate


def _req(ticket: str = "T-1", agent: str = "a") -> ApprovalRequest:
    return ApprovalRequest(ticket_id=ticket, agent=agent, operation="edit",
                           target="C:/game/x.ini", kind="write",
                           summary="修改配置")


def test_approve_resumes_request() -> None:
    async def scenario() -> bool:
        gate = ApprovalGate()
        task = asyncio.create_task(gate.request(_req()))
        await asyncio.sleep(0.02)  # 等其登记挂起
        pending = gate.list_pending()
        assert len(pending) == 1
        assert pending[0].agent == "a"
        assert gate.decide(pending[0].id, True)
        decision = await task
        assert gate.list_pending() == []
        return decision.approved

    assert asyncio.run(scenario()) is True


def test_reject_passes_reason() -> None:
    async def scenario() -> None:
        gate = ApprovalGate()
        task = asyncio.create_task(gate.request(_req(agent="b")))
        await asyncio.sleep(0.02)
        rid = gate.list_pending()[0].id
        assert gate.decide(rid, False, "不想执行")
        decision = await task
        assert decision.approved is False
        assert decision.reason == "不想执行"
        assert decision.timeout is False

    asyncio.run(scenario())


def test_timeout_auto_rejects() -> None:
    async def scenario() -> None:
        gate = ApprovalGate(ttl=0.05)
        decision = await gate.request(_req())
        assert decision.approved is False
        assert decision.timeout is True
        assert "超时" in decision.reason
        assert gate.list_pending() == []

    asyncio.run(scenario())


def test_parallel_requests_independent() -> None:
    async def scenario() -> None:
        gate = ApprovalGate()
        tasks = [
            asyncio.create_task(gate.request(_req(agent=f"a{i}")))
            for i in range(4)
        ]
        await asyncio.sleep(0.02)
        pending = gate.list_pending()
        assert len(pending) == 4

        # 0/2 批准，1/3 拒绝
        for i, p in enumerate(pending):
            assert gate.decide(p.id, approved=(i % 2 == 0))
        decisions = await asyncio.gather(*tasks)
        assert [d.approved for d in decisions] == [True, False, True, False]

    asyncio.run(scenario())


def test_ticket_filter_and_unknown_decide() -> None:
    async def scenario() -> None:
        gate = ApprovalGate()
        t1 = asyncio.create_task(gate.request(_req(ticket="T1")))
        t2 = asyncio.create_task(gate.request(_req(ticket="T2")))
        await asyncio.sleep(0.02)
        assert len(gate.list_pending("T1")) == 1
        rid = gate.list_pending("T1")[0].id
        # 未知 id
        assert gate.decide("nope", True) is False
        # 重复决定
        assert gate.decide(rid, True) is True
        assert gate.decide(rid, False) is False
        rid2 = gate.list_pending("T2")[0].id
        gate.decide(rid2, True)
        assert (await t1).approved and (await t2).approved

    asyncio.run(scenario())


def test_no_busy_wait_in_source() -> None:
    """TR-2.5：挂起必须基于事件，源码不得出现轮询 sleep。"""
    src = (Path(__file__).resolve().parents[1]
           / "src" / "gamedoctor" / "sandbox" / "gate.py").read_text(encoding="utf-8")
    assert "asyncio.sleep" not in src
    assert "wait_for" in src


def test_singleton_reset() -> None:
    reset_gate()
    g1 = get_gate()
    assert get_gate() is g1
    reset_gate()
    assert get_gate() is not g1
    reset_gate()
