"""流程编排引擎测试：条件边 / 反思回路 / 检查点。"""

import asyncio

from devlens.config import SandboxSettings
from devlens.core.agent import BaseAgent
from devlens.core.context import ContextManager
from devlens.core.events import EventBus, EventType
from devlens.llm.base import LLMProvider, LLMResponse
from devlens.llm.mock_policies import VERDICT_MARK
from devlens.tools.base import ToolRegistry
from devlens.workflow.engine import END, FlowEngine, FlowGraph, FlowNode
from devlens.workflow.graphs import _absorb_verdict, make_critic_route


class ScriptedLLM(LLMProvider):
    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)

    async def chat(self, messages, tools=None, agent="default", temperature=0.2):
        return self.responses.pop(0)


def _text(s):
    return LLMResponse(content=s)


def _agent(llm):
    return BaseAgent(role="x", system_prompt="s", allowed_tools=[],
                     llm=llm, tools=ToolRegistry(SandboxSettings()),
                     bus=EventBus(), ctx=ContextManager(), task_id="T")


def _graph(max_reflections: int) -> FlowGraph:
    g = FlowGraph(entry="worker")
    g.add_node(FlowNode("worker", "worker", build_task=lambda s: "写报告"))
    g.add_node(FlowNode("critic", "critic", build_task=lambda s: "评审",
                        absorb=_absorb_verdict, route=make_critic_route(max_reflections)))
    g.add_edge("worker", "critic")
    g.add_edge("critic", END, branch="pass")
    g.add_edge("critic", "worker", branch="revise")
    return g


def test_reflection_loop_bounded():
    import json

    revise = json.dumps({"verdict": "revise", "score": 40, "feedback": "证据不足"}, ensure_ascii=False)
    passing = json.dumps({"verdict": "pass", "score": 90, "feedback": "ok"}, ensure_ascii=False)
    agents = {
        "worker": _agent(ScriptedLLM([_text("报告v1"), _text("报告v2")])),
        "critic": _agent(ScriptedLLM([_text(revise + f"\n{VERDICT_MARK}"), _text(passing)])),
    }
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        engine = FlowEngine(_graph(max_reflections=1), agents, EventBus(), max_rounds=10, runs_dir=Path(td))
        state = asyncio.run(engine.run({"id": "T-REFLECT"}))

    assert state.status == "finished"
    assert state.revisits.get("reflection") == 1
    assert state.rounds == 4  # worker, critic, worker, critic
    assert state.blackboard["last_verdict"]["verdict"] == "pass"


def test_reflection_budget_exhausted_delivers_anyway():
    import json

    revise = json.dumps({"verdict": "revise", "score": 30, "feedback": "还是不行"}, ensure_ascii=False)
    agents = {
        "worker": _agent(ScriptedLLM([_text("报告v1"), _text("报告v2")])),
        "critic": _agent(ScriptedLLM([_text(revise), _text(revise)])),
    }
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        engine = FlowEngine(_graph(max_reflections=1), agents, EventBus(), max_rounds=10, runs_dir=Path(td))
        state = asyncio.run(engine.run({"id": "T-BUDGET"}))
    assert state.status == "finished"
    assert state.revisits.get("reflection") == 1
    # 第二次仍 revise，但预算耗尽 -> 强制 pass 分支
    assert state.blackboard["last_verdict"]["verdict"] == "revise"


def test_max_rounds_guard():
    revise = '{"verdict": "revise", "score": 1, "feedback": "x"}'
    # 无限 revise + 高预算 -> max_rounds 兜底
    agents = {
        "worker": _agent(ScriptedLLM([_text("r")] * 50)),
        "critic": _agent(ScriptedLLM([_text(revise)] * 50)),
    }
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        engine = FlowEngine(_graph(max_reflections=100), agents, EventBus(), max_rounds=5, runs_dir=Path(td))
        state = asyncio.run(engine.run({"id": "T-LOOP"}))
    assert state.status == "failed" and "最大轮数" in state.error


def test_checkpoint_resume(tmp_path):
    passing = '{"verdict": "pass", "score": 99, "feedback": "ok"}'
    agents = {
        "worker": _agent(ScriptedLLM([_text("报告")])),
        "critic": _agent(ScriptedLLM([_text(passing)])),
    }
    engine = FlowEngine(_graph(1), agents, EventBus(), max_rounds=10, runs_dir=tmp_path)
    state = asyncio.run(engine.run({"id": "T-CKPT"}))
    assert (tmp_path / "T-CKPT" / "state.json").exists()

    # 已完成的任务 resume：直接从检查点载入，不再执行新节点
    agents2 = {
        "worker": _agent(ScriptedLLM([_text("不应被调用")])),
        "critic": _agent(ScriptedLLM([_text("不应被调用")])),
    }
    engine2 = FlowEngine(_graph(1), agents2, EventBus(), max_rounds=10, runs_dir=tmp_path)
    state2 = asyncio.run(engine2.run({"id": "T-CKPT"}, resume=True))
    assert state2.rounds == state.rounds
    assert state2.status == "finished"


def test_events_flow():
    passing = '{"verdict": "pass", "score": 99, "feedback": "ok"}'
    bus = EventBus()
    types = []
    bus.subscribe_fn(lambda e: types.append(e.type))
    agents = {
        "worker": _agent(ScriptedLLM([_text("报告")])),
        "critic": _agent(ScriptedLLM([_text(passing)])),
    }
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        engine = FlowEngine(_graph(1), agents, bus, max_rounds=10, runs_dir=Path(td))
        asyncio.run(engine.run({"id": "T-EVT"}))
    assert EventType.TASK_STARTED in types
    assert EventType.NODE_ENTERED in types
    assert EventType.TASK_FINISHED in types
