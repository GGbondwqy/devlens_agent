"""端到端测试：Mock 模型驱动三大研发场景全流程。"""

import asyncio
import json

import pytest

from devlens.config import load_settings
from devlens.runner import TaskRunner

pytestmark = pytest.mark.usefixtures("fixture_repo")


def _run(scenario: str, input_path: str):
    from devlens.config import PROJECT_ROOT

    data = json.loads((PROJECT_ROOT / input_path).read_text(encoding="utf-8"))
    runner = TaskRunner(load_settings())
    state = asyncio.run(runner.run_task(scenario, data))
    return runner, state


def test_issue_scenario_e2e():
    runner, state = _run("issue", "fixtures/issues/ISSUE-101.json")
    assert state.status == "finished"
    report = runner.store.pick_report(state)
    assert "discount.py" in report and "根因" in report
    assert len(state.evidence()) >= 2
    assert state.blackboard["last_verdict"]["verdict"] == "pass"
    # 产物落盘
    tdir = runner.store.task_dir("ISSUE-101")
    assert (tdir / "result.md").exists()
    assert (tdir / "state.json").exists()
    assert (tdir / "events.jsonl").exists()
    assert (tdir / "summary.json").exists()


def test_pr_scenario_e2e():
    runner, state = _run("pr", "fixtures/prs/PR-42.json")
    assert state.status == "finished"
    report = runner.store.pick_report(state)
    assert "except Exception" in report
    assert "严重" in report and "Request Changes" in report
    assert state.blackboard["last_verdict"]["verdict"] == "pass"


def test_ci_scenario_e2e():
    runner, state = _run("ci", "fixtures/ci/JOB-88.json")
    assert state.status == "finished"
    report = runner.store.pick_report(state)
    assert "test_percent_discount" in report
    assert "discount.py" in report
    assert len(state.evidence()) >= 2


def test_tool_events_present_in_transcript():
    from devlens.core.events import EventType
    from devlens.config import PROJECT_ROOT

    events = [
        json.loads(ln)
        for ln in (PROJECT_ROOT / ".runs" / "ISSUE-101" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = {e["type"] for e in events}
    assert EventType.TASK_STARTED.value in types
    assert EventType.TOOL_CALLED.value in types
    assert EventType.TOOL_RESULT.value in types
    assert EventType.TASK_FINISHED.value in types


def test_eval_full_score():
    from devlens.eval.harness import run_eval

    report = asyncio.run(run_eval())
    assert report["total_score"] == 100
