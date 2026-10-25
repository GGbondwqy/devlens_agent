"""任务台账与运行产物持久化。

每个任务在 runs_dir/<task_id>/ 下留痕：
    state.json     —— 流程状态检查点（可续跑）
    events.jsonl   —— 事件流水（可回放）
    result.md      —— 最终交付物（归因报告 / 评审意见 / 失败归因）
    summary.json   —— 元数据（场景、轮次、token 消耗、裁决）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devlens.workflow.engine import FlowState

REPORT_NODES = ("analyze", "review", "troubleshoot")


class RunsStore:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        return self.runs_dir / task_id

    # ---- 产物 ----
    def pick_report(self, state: FlowState) -> str:
        for node in REPORT_NODES:
            if state.node_outputs.get(node):
                return state.node_outputs[node]
        return state.node_outputs.get("plan", "")

    def save_result(self, state: FlowState) -> Path:
        tdir = self.task_dir(state.task_id)
        tdir.mkdir(parents=True, exist_ok=True)
        report = self.pick_report(state)
        (tdir / "result.md").write_text(report or "（无产出）", encoding="utf-8")
        last_verdict = state.blackboard.get("last_verdict", {})
        summary = {
            "task_id": state.task_id,
            "scenario": state.scenario,
            "status": state.status,
            "rounds": state.rounds,
            "evidence_count": len(state.evidence()),
            "verdict": last_verdict.get("verdict"),
            "score": last_verdict.get("score"),
            "nodes": list(state.node_outputs),
        }
        (tdir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return tdir / "result.md"

    # ---- 查询 ----
    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = []
        for tdir in sorted(self.runs_dir.iterdir()):
            summary = tdir / "summary.json"
            if tdir.is_dir() and summary.exists():
                try:
                    tasks.append(json.loads(summary.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    continue
        return tasks

    def get_state(self, task_id: str) -> FlowState | None:
        path = self.task_dir(task_id) / "state.json"
        if not path.exists():
            return None
        try:
            return FlowState.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None

    def read_events(self, task_id: str) -> list[dict[str, Any]]:
        path = self.task_dir(task_id) / "events.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
