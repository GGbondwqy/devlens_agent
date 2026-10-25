"""三大研发场景的流程图定义（垂直业务层）。

场景与流程（对应调研中 DevFlow AI 的三大场景 + 企业最旺的 CR 场景）：

    issue（Issue 归因分析）：
        plan(规划) -> research(检索) -> analyze(归因) -> critic(质检) -pass-> END
                                                          ^----revise----+
    pr（PR 代码评审，企业需求最旺的 CR 场景）：
        review(评审) -> critic(质检) -pass-> END
             ^----------revise----------+
    ci（CI 失败排查）：
        troubleshoot(排障) -> critic(质检) -pass-> END
                ^--------------revise----------+

反思回路受 max_reflections 预算约束，防止无限循环。
"""

from __future__ import annotations

import json
import re
from typing import Callable

from devlens.llm.mock_policies import EVIDENCE_MARK, VERDICT_MARK
from devlens.workflow.engine import END, FlowGraph, FlowNode, FlowState

ReportAbsorber = Callable[[FlowState, str], None]


# ---------------------------------------------------------------- 黑板吸收器
def _absorb_plan(state: FlowState, output: str) -> None:
    state.blackboard["plan"] = output


def _absorb_evidence(state: FlowState, output: str) -> None:
    """从 Agent 产出中解析 EVIDENCE 行，写入共享黑板。"""
    lines = [ln for ln in output.splitlines() if ln.startswith(EVIDENCE_MARK)]
    state.add_evidence(lines)


def _absorb_verdict(state: FlowState, output: str) -> None:
    """解析 Critic 的 JSON 裁决，记录到黑板。"""
    m = re.search(r"\{.*\}", output, flags=re.S)
    if not m:
        return
    try:
        payload = json.loads(m.group(0))
    except json.JSONDecodeError:
        return
    payload.pop(VERDICT_MARK, None)
    state.blackboard["last_verdict"] = payload
    state.blackboard.setdefault("verdicts", []).append(payload)


# ---------------------------------------------------------------- 上下文构造
def _bg_plan_only(state: FlowState) -> str:
    return state.blackboard.get("plan", "（无规划）")


def _bg_for_issue_analyst(state: FlowState) -> str:
    parts = [f"[任务拆解]\n{state.blackboard.get('plan', '（无）')}"]
    research = state.node_outputs.get("research", "")
    if research:
        parts.append(f"[检索者原始产出]\n{research[:1500]}")
    if state.evidence():
        parts.append("[证据清单]\n" + "\n".join(state.evidence()))
    return "\n\n".join(parts)


def _bg_for_report(state: FlowState) -> str:
    """给 Critic 的上下文：待评审的报告 + 证据清单。"""
    report = ""
    for node in ("analyze", "review", "troubleshoot"):
        if state.node_outputs.get(node):
            report = state.node_outputs[node]
    evid = "\n".join(state.evidence()) or "（无）"
    return f"[待评审产出]\n{report}\n\n[证据清单]\n{evid}"


# ---------------------------------------------------------------- 路由
def make_critic_route(max_reflections: int):
    def route(state: FlowState, result) -> str:
        verdict = state.blackboard.get("last_verdict", {})
        if verdict.get("verdict") == "revise":
            used = state.revisits.get("reflection", 0)
            if used < max_reflections:
                state.revisits["reflection"] = used + 1
                return "revise"
            return "pass"  # 反思预算耗尽：带病交付并记录
        return "pass"

    return route


# ---------------------------------------------------------------- 任务生成
def _t_plan(state: FlowState) -> str:
    d = state.input
    return f"请拆解以下研发任务并说明执行路径。\n场景: {state.scenario}\n任务ID: {d.get('id')}\n标题: {d.get('title', '')}\n详情: {d.get('body') or d.get('description', '')}"


def _t_research(state: FlowState) -> str:
    d = state.input
    return (
        f"请检索与问题相关的代码与项目文档。\n问题标题: {d.get('title', '')}\n"
        f"问题正文:\n{d.get('body', '')}\n"
        "要求：产出至少两条可追溯证据（EVIDENCE 行），代码证据与文档/日志证据兼备。"
    )


def _t_analyze(state: FlowState) -> str:
    return f"请基于上游上下文（任务拆解 / 检索者产出 / 证据清单）产出归因分析报告。\n问题ID: {state.input.get('id')}，标题: {state.input.get('title', '')}"


def _t_review(state: FlowState) -> str:
    d = state.input
    return (
        f"请评审以下 Pull Request 并输出结构化评审意见。\nPR: {d.get('id')}\n"
        f"标题: {d.get('title', '')}\n描述: {d.get('description', '')}\n"
        f"基准分支: {d.get('base', 'main')}，变更分支: {d.get('head', '')}\n"
        "要求：每条问题引用 file:line，给出风险分级与最终结论。"
    )


def _t_troubleshoot(state: FlowState) -> str:
    d = state.input
    return (
        f"请排查失败的 CI 流水线并输出失败归因。\n流水线: {d.get('pipeline', '')}\n"
        f"任务ID: {d.get('id')}\n日志路径: {d.get('log', '')}\n"
        "要求：定位失败用例与根因（引用 file:line），区分代码缺陷与测试自身问题。"
    )


def _t_critic(state: FlowState) -> str:
    return "请评审以上产出是否达到交付标准（证据充分性 / 结论完整性 / 可追溯性），输出裁决 JSON。"


# ---------------------------------------------------------------- 场景图
def build_issue_graph(max_reflections: int) -> FlowGraph:
    g = FlowGraph(entry="plan")
    g.scenario = "issue"  # type: ignore[attr-defined]
    g.add_node(FlowNode("plan", "planner", build_task=_t_plan, absorb=_absorb_plan))
    g.add_node(
        FlowNode(
            "research",
            "researcher",
            build_task=_t_research,
            background=_bg_plan_only,
            absorb=_absorb_evidence,
        )
    )
    g.add_node(
        FlowNode("analyze", "analyst", build_task=_t_analyze, background=_bg_for_issue_analyst, absorb=_absorb_evidence)
    )
    g.add_node(
        FlowNode(
            "critic",
            "critic",
            build_task=_t_critic,
            background=_bg_for_report,
            absorb=_absorb_verdict,
            route=make_critic_route(max_reflections),
        )
    )
    g.add_edge("plan", "research")
    g.add_edge("research", "analyze")
    g.add_edge("analyze", "critic")
    g.add_edge("critic", END, branch="pass")
    g.add_edge("critic", "research", branch="revise")
    return g


def build_pr_graph(max_reflections: int) -> FlowGraph:
    g = FlowGraph(entry="review")
    g.scenario = "pr"  # type: ignore[attr-defined]
    g.add_node(
        FlowNode("review", "reviewer", build_task=_t_review, absorb=_absorb_evidence)
    )
    g.add_node(
        FlowNode(
            "critic",
            "critic",
            build_task=_t_critic,
            background=_bg_for_report,
            absorb=_absorb_verdict,
            route=make_critic_route(max_reflections),
        )
    )
    g.add_edge("review", "critic")
    g.add_edge("critic", END, branch="pass")
    g.add_edge("critic", "review", branch="revise")
    return g


def build_ci_graph(max_reflections: int) -> FlowGraph:
    g = FlowGraph(entry="troubleshoot")
    g.scenario = "ci"  # type: ignore[attr-defined]
    g.add_node(
        FlowNode("troubleshoot", "troubleshooter", build_task=_t_troubleshoot, absorb=_absorb_evidence)
    )
    g.add_node(
        FlowNode(
            "critic",
            "critic",
            build_task=_t_critic,
            background=_bg_for_report,
            absorb=_absorb_verdict,
            route=make_critic_route(max_reflections),
        )
    )
    g.add_edge("troubleshoot", "critic")
    g.add_edge("critic", END, branch="pass")
    g.add_edge("critic", "troubleshoot", branch="revise")
    return g


def build_graph(scenario: str, max_reflections: int = 1) -> FlowGraph:
    builders = {
        "issue": build_issue_graph,
        "pr": build_pr_graph,
        "ci": build_ci_graph,
    }
    if scenario not in builders:
        raise ValueError(f"未知场景: {scenario}（支持: {', '.join(builders)}）")
    return builders[scenario](max_reflections)
