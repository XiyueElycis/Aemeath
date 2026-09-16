"""临时校验脚本：打印四个工作流模板的结构，核对顺序与门控。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gamedoctor.orchestrator.orchestrator import (
    GameAgentOrchestrator,
    build_workflow,
    detect_intent,
    WORKFLOW_LABELS,
)


def describe_task(task):
    flag = "?" if task.optional else ""
    req = f"<-{task.requires}" if task.requires else ""
    return f"{task.name}{flag}(p{task.priority}){req}"


def main():
    orch = GameAgentOrchestrator()
    for name in sorted(WORKFLOW_LABELS):
        plan = build_workflow(orch, name, "TestGame", "", "C:/games/test")
        total = sum(len(s.tasks) for s in plan.stages)
        print(f"\n=== {name}: {WORKFLOW_LABELS[name]} ===")
        print(f"stages={len(plan.stages)} tasks={total}")
        for stage in plan.stages:
            deps = ",".join(stage.dependencies) or "-"
            mode = "par" if stage.parallel else "seq"
            print(f"  [{stage.name}] {mode} deps={deps}  {stage.description}")
            for task in stage.tasks:
                print(f"      - {describe_task(task)}")

    print("\n=== 意图分流 ===")
    samples = [
        "游戏很卡，帧率低",
        "帮我装一下这个游戏",
        "启动报错崩溃",
        "做个日常维护巡检",
        "没声音",
        "你好",
        "",
    ]
    for text in samples:
        print(f"  {text!r:24s} -> {detect_intent(text)}")

    print("\n=== 未知模板应抛错 ===")
    try:
        build_workflow(orch, "nope", "TestGame")
        print("  [FAIL] 未抛错")
    except ValueError as exc:
        print(f"  [OK] {exc}")


if __name__ == "__main__":
    main()
