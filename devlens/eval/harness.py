"""离线评测 harness：固定案例 + 期望断言 + 打分报告。

为什么要有评测（调研结论）：评测能力正成为企业选型 AI 编程产品的核心门槛
（SWE-bench 之于 OpenHands/SWE-agent，PR 回放之于 CodeRabbit）。
本 harness 用「确定性案例 + 结构断言」验证框架行为的正确性；
接真实模型后，同一套断言即可度量模型效果（准确率/证据覆盖率）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devlens.config import PROJECT_ROOT, Settings, load_settings
from devlens.memory.local import REPORT_NODES, RunsStore
from devlens.runner import TaskRunner
from devlens.workflow.engine import FlowState

FIXTURES = PROJECT_ROOT / "fixtures"


@dataclass
class EvalCheck:
    kind: str  # report_contains | evidence_min | status | verdict
    arg: Any
    desc: str


@dataclass
class EvalCase:
    case_id: str
    scenario: str
    input_file: Path
    checks: list[EvalCheck] = field(default_factory=list)


def _default_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="issue-101",
            scenario="issue",
            input_file=FIXTURES / "issues" / "ISSUE-101.json",
            checks=[
                EvalCheck("report_contains", "discount.py", "报告定位到 discount.py"),
                EvalCheck("report_contains", "根因", "报告包含根因章节"),
                EvalCheck("report_contains", "修复建议", "报告包含修复建议"),
                EvalCheck("evidence_min", 2, "证据链 ≥2 条"),
                EvalCheck("verdict", "pass", "Critic 裁决通过"),
            ],
        ),
        EvalCase(
            case_id="pr-42",
            scenario="pr",
            input_file=FIXTURES / "prs" / "PR-42.json",
            checks=[
                EvalCheck("report_contains", "except Exception", "评审发现吞异常问题"),
                EvalCheck("report_contains", "严重", "问题带严重级别"),
                EvalCheck("report_contains", "Request Changes", "结论为 Request Changes"),
                EvalCheck("evidence_min", 1, "评审意见挂证据"),
                EvalCheck("verdict", "pass", "Critic 裁决通过"),
            ],
        ),
        EvalCase(
            case_id="ci-job-88",
            scenario="ci",
            input_file=FIXTURES / "ci" / "JOB-88.json",
            checks=[
                EvalCheck("report_contains", "test_percent_discount", "定位到失败用例"),
                EvalCheck("report_contains", "discount.py", "归因到 discount.py"),
                EvalCheck("report_contains", "修复建议", "包含修复建议"),
                EvalCheck("evidence_min", 1, "归因挂证据"),
                EvalCheck("verdict", "pass", "Critic 裁决通过"),
            ],
        ),
    ]


def _evaluate(state: FlowState, report: str, check: EvalCheck) -> bool:
    if check.kind == "report_contains":
        return check.arg in report
    if check.kind == "evidence_min":
        return len(state.evidence()) >= int(check.arg)
    if check.kind == "status":
        return state.status == check.arg
    if check.kind == "verdict":
        return state.blackboard.get("last_verdict", {}).get("verdict") == check.arg
    return False


async def run_eval(settings: Settings | None = None, cases: list[EvalCase] | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    runner = TaskRunner(settings)
    store = runner.store
    cases = cases or _default_cases()
    ts = time.strftime("%Y%m%d-%H%M%S")

    results: list[dict[str, Any]] = []
    for case in cases:
        data = json.loads(case.input_file.read_text(encoding="utf-8"))
        state = await runner.run_task(
            scenario=case.scenario,
            data=data,
            task_id=f"eval-{case.case_id}-{ts}",
        )
        report = store.pick_report(state)
        checks = [
            {
                "desc": c.desc,
                "passed": _evaluate(state, report, c),
            }
            for c in case.checks
        ]
        passed = sum(1 for c in checks if c["passed"])
        results.append(
            {
                "case": case.case_id,
                "scenario": case.scenario,
                "status": state.status,
                "rounds": state.rounds,
                "evidence": len(state.evidence()),
                "checks": checks,
                "passed": passed,
                "total": len(checks),
                "score": round(passed / len(checks) * 100) if checks else 0,
            }
        )

    total_score = round(sum(r["score"] for r in results) / len(results)) if results else 0
    report_out = {
        "generated_at": ts,
        "provider": settings.llm.provider,
        "total_score": total_score,
        "cases": results,
    }
    out_path = PROJECT_ROOT / "eval_report.json"
    out_path.write_text(json.dumps(report_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_out


def render_text(report: dict[str, Any]) -> str:
    lines = [f"DevLens 离线评测报告（provider={report['provider']}）  总分: {report['total_score']}", ""]
    for case in report["cases"]:
        lines.append(
            f"· [{case['scenario']}] {case['case']}: {case['score']} 分"
            f"（状态 {case['status']}，{case['rounds']} 轮，证据 {case['evidence']} 条）"
        )
        for c in case["checks"]:
            mark = "PASS" if c["passed"] else "FAIL"
            lines.append(f"    [{mark}] {c['desc']}")
    return "\n".join(lines)


__all__ = ["run_eval", "render_text", "EvalCase", "EvalCheck", "RunsStore", "REPORT_NODES"]
