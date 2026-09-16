"""approvals / sandbox HTTP 端点测试（TR-5.1~5.4）。

授权门的挂起/唤醒与事件循环绑定，happy path 用同一 loop 直接调用端点函数；
状态码路径（400/404/409）与会话文件操作走 FastAPI TestClient。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gamedoctor.sandbox import ApprovalDecision, ApprovalGate, ApprovalRequest, SandboxSession
from gamedoctor.sandbox import io as sxio
from gamedoctor.sandbox.gate import get_gate, reset_gate
from gamedoctor.sandbox.session import _ACTIVE_SESSIONS
from gamedoctor.server import (
    ApprovalDecisionRequest,
    app,
    decide_approval,
    list_pending_approvals,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    apphome = tmp_path / "apphome"
    monkeypatch.setattr("gamedoctor.sandbox.session.app_dir", lambda: apphome)
    reset_gate()
    _ACTIVE_SESSIONS.clear()
    game = tmp_path / "game"
    game.mkdir()
    yield TestClient(app), game
    reset_gate()
    _ACTIVE_SESSIONS.clear()


def _session(ticket: str, game: Path) -> SandboxSession:
    return SandboxSession(ticket, game)


# --------------------------------------------------------------------- #
# TR-5.1 pending → decide 全流程（同 loop 直调端点函数）
# --------------------------------------------------------------------- #
def test_approval_pending_decide_flow() -> None:
    reset_gate()

    async def case():
        gate = get_gate()
        req = ApprovalRequest(
            ticket_id="T-1", agent="script_editor", operation="edit",
            target="config.ini", kind="write", summary="测试授权")
        task = asyncio.ensure_future(gate.request(req))
        await asyncio.sleep(0.02)

        listed = list_pending_approvals()
        assert listed["count"] == 1
        item = listed["items"][0]
        assert item["id"] == req.id and item["kind"] == "write"
        assert list_pending_approvals(ticket_id="OTHER")["count"] == 0

        resp = decide_approval(req.id, ApprovalDecisionRequest(decision="approve"))
        assert resp["accepted"] is True
        decision = await asyncio.wait_for(task, timeout=1)
        assert decision.approved is True

    asyncio.run(case())


def test_approval_http_status_codes(client) -> None:
    http, _ = client
    # 未知 id → 404
    r = http.post("/approvals/nope", json={"decision": "approve"})
    assert r.status_code == 404

    # 非法 decision → 400
    gate = get_gate()
    gate._decided["seen"] = ApprovalDecision(approved=True, request_id="seen")
    r = http.post("/approvals/seen", json={"decision": "maybe"})
    assert r.status_code == 400

    # 已决 id → 409
    r = http.post("/approvals/seen", json={"decision": "reject"})
    assert r.status_code == 409

    # pending 列表结构
    r = http.get("/approvals/pending")
    assert r.status_code == 200 and r.json()["items"] == []


# --------------------------------------------------------------------- #
# 会话详情 / 预览
# --------------------------------------------------------------------- #
def test_session_detail_with_preview(client) -> None:
    http, game = client
    (game / "config.ini").write_text("old", encoding="utf-8")
    s = _session("T-DETAIL", game)
    sxio.write_text(s, game / "config.ini", "new-content")
    sxio.write_text(s, game / "brandnew.txt", "fresh")
    sxio.ensure_dir(s, game / "newdir")
    s.seal()
    _ACTIVE_SESSIONS["T-DETAIL"] = s  # 模拟协调器登记的内存实例

    pend = http.get("/sandbox/pending").json()
    mem = {i["ticket_id"]: i for i in pend["items"]}
    assert "T-DETAIL" in mem and mem["T-DETAIL"]["change_count"] == 3

    r = http.get("/sandbox/sessions/T-DETAIL")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ready"
    by_path = {c["relpath"]: c for c in data["changes"]}
    assert by_path["config.ini"]["new_text"] == "new-content"
    assert by_path["config.ini"]["old_text"] == "old"
    assert by_path["brandnew.txt"]["new_text"] == "fresh"
    # 建目录变更无字节数：size 必须为 null（C# DTO 依赖此契约用 long?）
    assert by_path["newdir"]["op"] == "mkdir"
    assert by_path["newdir"]["size"] is None


def test_session_unknown_404(client) -> None:
    http, _ = client
    assert http.get("/sandbox/sessions/GHOST").status_code == 404
    assert http.post("/sandbox/sessions/GHOST/apply").status_code == 404


# --------------------------------------------------------------------- #
# running 会话应用/丢弃 → 409
# --------------------------------------------------------------------- #
def test_running_session_actions_conflict(client) -> None:
    http, game = client
    _session("T-RUN", game)  # 未封存
    assert http.post("/sandbox/sessions/T-RUN/apply").status_code == 409
    assert http.post("/sandbox/sessions/T-RUN/discard").status_code == 409


# --------------------------------------------------------------------- #
# TR-5.4 apply 明细结构 + 真实落盘 + 备份
# --------------------------------------------------------------------- #
def test_apply_changes_details(client) -> None:
    http, game = client
    (game / "config.ini").write_text("old", encoding="utf-8")
    s = _session("T-APPLY", game)
    sxio.write_text(s, game / "config.ini", "new")
    s.seal()

    r = http.post("/sandbox/sessions/T-APPLY/apply")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied" and body["succeeded"] == 1 and body["failed"] == 0
    detail = body["details"][0]
    for key in ("relpath", "op", "ok", "error", "backup_path"):
        assert key in detail
    assert detail["ok"] is True and detail["op"] == "modify"
    assert (game / "config.ini").read_text(encoding="utf-8") == "new"
    assert detail["backup_path"] and Path(detail["backup_path"]).is_file()

    # 已应用再次 apply → 409
    assert http.post("/sandbox/sessions/T-APPLY/apply").status_code == 409


def test_discard_changes_zero_touch(client) -> None:
    http, game = client
    (game / "config.ini").write_text("old", encoding="utf-8")
    s = _session("T-DISCARD", game)
    sxio.write_text(s, game / "config.ini", "new")
    s.seal()

    r = http.post("/sandbox/sessions/T-DISCARD/discard")
    assert r.status_code == 200 and r.json()["discarded"] is True
    assert (game / "config.ini").read_text(encoding="utf-8") == "old"
    # 沙箱目录已删，会话不可再查
    assert http.get("/sandbox/sessions/T-DISCARD").status_code == 404


# --------------------------------------------------------------------- #
# TR-5.3 模拟重启：内存注册表清空后靠磁盘 meta 列出并 apply
# --------------------------------------------------------------------- #
def test_restart_recovery_from_disk(client) -> None:
    http, game = client
    (game / "a.txt").write_text("old-a", encoding="utf-8")
    s = _session("T-RESTART", game)
    sxio.write_text(s, game / "a.txt", "new-a")
    s.seal()

    # 进程重启：内存登记全空
    _ACTIVE_SESSIONS.clear()

    r = http.get("/sandbox/pending")
    assert r.status_code == 200
    ids = {item["ticket_id"] for item in r.json()["items"]}
    assert "T-RESTART" in ids

    r = http.post("/sandbox/sessions/T-RESTART/apply")
    assert r.status_code == 200 and r.json()["succeeded"] == 1
    assert (game / "a.txt").read_text(encoding="utf-8") == "new-a"
