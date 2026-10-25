"""自研流程编排引擎：状态机 + 条件边 + 反思回路 + 检查点。

为什么自研而不直接用 LangGraph（调研后的取舍）：
    * 编排语义只需要「节点 + 条件边 + 受限回路 + 检查点」，自研 200 行内可做到
      完全可控、可测试、可讲解，避免为 20% 能力引入 80% 的框架复杂度；
    * 接口保持 LangGraph 风格（StateGraph = FlowGraph, add_conditional_edges =
      route 分支），需要迁移时是平移而不是重写。

核心概念：
    FlowNode  —— 一个流程节点，包装一个角色 Agent：
                 build_task(生成任务) -> Agent 循环 -> absorb(吸收产出进黑板) -> route(决定分支)
    FlowGraph —— 节点 + 条件边（node, branch) -> next_node
    FlowState —— 可序列化状态（黑板 + 节点产出 + 回路计数），支持检查点续跑
    FlowEngine—— 驱动循环，发事件、存检查点、防死循环
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from devlens.core.agent import AgentResult, BaseAgent
from devlens.core.events import Event, EventBus, EventType

END = "END"


class FlowState(BaseModel):
    task_id: str
    scenario: str
    input: dict[str, Any] = field(default_factory=dict)
    blackboard: dict[str, Any] = field(default_factory=dict)  # 共享黑板：plan/evidence/verdicts
    node_outputs: dict[str, str] = field(default_factory=dict)
    revisits: dict[str, int] = field(default_factory=dict)
    rounds: int = 0
    current: str | None = None
    status: str = "pending"  # pending | running | finished | failed
    error: str | None = None

    # ---- 黑板便捷方法 ----
    def add_evidence(self, lines: list[str]) -> int:
        pool = self.blackboard.setdefault("evidence", [])
        added = 0
        for ln in lines:
            if ln not in pool:
                pool.append(ln)
                added += 1
        return added

    def evidence(self) -> list[str]:
        return list(self.blackboard.get("evidence", []))

    def digest(self, max_chars: int = 1600) -> str:
        """黑板摘要：作为下游 Agent 的「上游上下文」。"""
        parts: list[str] = []
        if self.blackboard.get("plan"):
            parts.append(f"[任务拆解]\n{self.blackboard['plan']}")
        if self.evidence():
            parts.append("[证据清单]\n" + "\n".join(self.evidence()))
        for key in ("upstream_report",):
            if self.blackboard.get(key):
                parts.append(f"[上游产出]\n{self.blackboard[key][:max_chars]}")
        return "\n\n".join(parts) if parts else "（无上游上下文）"


# 节点钩子类型
TaskBuilder = Callable[[FlowState], str]
BackgroundBuilder = Callable[[FlowState], str]
Absorber = Callable[[FlowState, str], None]
Router = Callable[[FlowState, AgentResult], str]


@dataclass
class FlowNode:
    name: str
    agent_role: str
    build_task: TaskBuilder
    background: BackgroundBuilder | None = None
    absorb: Absorber | None = None
    route: Router | None = None


@dataclass
class FlowGraph:
    entry: str
    nodes: dict[str, FlowNode] = field(default_factory=dict)
    edges: dict[str, dict[str, str]] = field(default_factory=dict)

    def add_node(self, node: FlowNode) -> "FlowGraph":
        self.nodes[node.name] = node
        return self

    def add_edge(self, from_node: str, to: str, branch: str = "next") -> "FlowGraph":
        self.edges.setdefault(from_node, {})[branch] = to
        return self

    def next_of(self, node: str, branch: str) -> str | None:
        return self.edges.get(node, {}).get(branch)


class FlowEngine:
    def __init__(
        self,
        graph: FlowGraph,
        agents: dict[str, BaseAgent],
        bus: EventBus,
        runs_dir: Path,
        max_rounds: int = 12,
    ) -> None:
        self.graph = graph
        self.agents = agents
        self.bus = bus
        self.runs_dir = runs_dir
        self.max_rounds = max_rounds

    # ---- 检查点 ----
    def _checkpoint_path(self, task_id: str) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        return self.runs_dir / task_id / "state.json"

    def save_checkpoint(self, state: FlowState) -> None:
        path = self._checkpoint_path(state.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        self.bus.emit(
            Event(type=EventType.CHECKPOINT_SAVED, task_id=state.task_id, message=str(path))
        )

    def load_checkpoint(self, task_id: str) -> FlowState | None:
        path = self._checkpoint_path(task_id)
        if not path.exists():
            return None
        try:
            return FlowState.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None

    # ---- 事件 ----
    def _emit(self, type_: EventType, task_id: str, message: str, **data) -> None:
        self.bus.emit(
            Event(
                type=type_,
                task_id=task_id,
                node=data.pop("node", None),
                agent=data.pop("agent", None),
                message=message,
                data=data,
            )
        )

    # ---- 主驱动 ----
    async def run(self, input: dict[str, Any], resume: bool = False) -> FlowState:
        task_id = str(input.get("id") or "TASK")
        state: FlowState | None = None
        if resume:
            state = self.load_checkpoint(task_id)
        if state is None:
            state = FlowState(task_id=task_id, scenario=self._scenario(), input=input)
            state.current = self.graph.entry
            state.status = "running"
            self._emit(EventType.TASK_STARTED, task_id, f"场景: {state.scenario}")

        state.status = "running"
        while state.current and state.current != END:
            if state.rounds >= self.max_rounds:
                state.error = f"超过最大轮数 {self.max_rounds}，流程终止"
                state.status = "failed"
                self._emit(EventType.TASK_FAILED, task_id, state.error)
                self.save_checkpoint(state)
                return state

            node = self.graph.nodes.get(state.current)
            if node is None:
                state.error = f"未知节点: {state.current}"
                state.status = "failed"
                self._emit(EventType.TASK_FAILED, task_id, state.error)
                return state

            self._emit(EventType.NODE_ENTERED, task_id, f"进入节点 {node.name}", node=node.name)
            agent = self.agents[node.agent_role]
            result = await agent.run(task=node.build_task(state), background=state.digest())
            state.node_outputs[node.name] = result.output

            if node.absorb:
                node.absorb(state, result.output)

            branch = node.route(state, result) if node.route else "next"
            if branch == "revise":
                self._emit(
                    EventType.REFLECTION,
                    task_id,
                    "Critic 打回重做: " + str(state.blackboard.get("last_verdict", {}).get("feedback", "")),
                    node=node.name,
                )

            self._emit(
                EventType.NODE_EXITED,
                task_id,
                f"节点 {node.name} 完成，分支: {branch}",
                node=node.name,
                branch=branch,
                steps=result.steps,
                tool_calls=result.tool_call_count,
            )
            state.rounds += 1
            state.current = self.graph.next_of(node.name, branch) or END
            self.save_checkpoint(state)

        state.status = "failed" if state.error else "finished"
        self._emit(
            EventType.TASK_FINISHED if state.status == "finished" else EventType.TASK_FAILED,
            task_id,
            f"流程结束（{state.rounds} 轮），状态: {state.status}",
        )
        self.save_checkpoint(state)
        return state

    def _scenario(self) -> str:
        # 引擎不感知场景语义，从 graph 元数据透传
        return getattr(self.graph, "scenario", "unknown")
