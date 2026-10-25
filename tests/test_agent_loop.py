"""Agent ReAct 循环测试（脚本化 LLM，不依赖真实模型）。"""

import pytest

from devlens.config import SandboxSettings
from devlens.core.agent import BaseAgent
from devlens.core.context import ContextManager
from devlens.core.events import EventBus, EventType
from devlens.llm.base import ChatMessage, LLMProvider, LLMResponse, ToolCall, Usage
from devlens.tools.base import ToolRegistry
from devlens.tools.file_tools import ReadFileTool
from devlens.tools.sandbox import Sandbox


class ScriptedLLM(LLMProvider):
    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    async def chat(self, messages, tools=None, agent="default", temperature=0.2):
        self.calls.append(list(messages))
        return self.responses.pop(0)


def _resp_text(text: str) -> LLMResponse:
    return LLMResponse(content=text, usage=Usage(prompt_tokens=10, completion_tokens=5))


def _resp_tool(name: str, args: dict) -> LLMResponse:
    return LLMResponse(
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id="t1", name=name, arguments=args)],
    )


@pytest.fixture
def registry(tmp_path):
    (tmp_path / "README.md").write_text("hello agent", encoding="utf-8")
    sb = Sandbox(tmp_path)
    reg = ToolRegistry(settings=SandboxSettings())
    reg.register(ReadFileTool(sb))
    return reg


def test_loop_tool_then_final(registry, tmp_path):
    bus = EventBus()
    seen = []
    bus.subscribe_fn(seen.append)
    llm = ScriptedLLM([_resp_tool("read_file", {"path": "README.md"}), _resp_text("文件内容是 hello agent")])
    agent = BaseAgent(
        role="tester", system_prompt="sys", allowed_tools=["read_file"],
        llm=llm, tools=registry, bus=bus, ctx=ContextManager(), task_id="T1",
    )
    import asyncio

    result = asyncio.run(agent.run("读一下 README", ""))
    assert result.success and result.output == "文件内容是 hello agent"
    assert result.steps == 2 and result.tool_call_count == 1
    # 消息回填结构：system -> user -> assistant(tool_calls) -> tool
    second_call = llm.calls[1]
    assert second_call[-1].role == "tool" and "hello agent" in (second_call[-1].content or "")
    types = [e.type for e in seen]
    assert EventType.TOOL_CALLED in types and EventType.TOOL_RESULT in types
    assert EventType.AGENT_THOUGHT in types


def test_loop_immediate_final(registry):
    import asyncio

    bus = EventBus()
    llm = ScriptedLLM([_resp_text("直接回答")])
    agent = BaseAgent(role="t", system_prompt="s", allowed_tools=["read_file"],
                      llm=llm, tools=registry, bus=bus, ctx=ContextManager(), task_id="T")
    result = asyncio.run(agent.run("q", "bg"))
    assert result.output == "直接回答" and result.steps == 1
    # 背景资料被注入 user 消息
    assert "bg" in (llm.calls[0][1].content or "")


def test_loop_max_steps_guard(registry):
    import asyncio

    bus = EventBus()
    llm = ScriptedLLM([_resp_tool("read_file", {"path": "README.md"}) for _ in range(10)])
    agent = BaseAgent(role="t", system_prompt="s", allowed_tools=["read_file"],
                      llm=llm, tools=registry, bus=bus, ctx=ContextManager(),
                      max_steps=3, task_id="T")
    result = asyncio.run(agent.run("q", ""))
    assert not result.success and result.stopped_reason == "max_steps"
    assert result.steps == 3


def test_llm_exception_surfaces_as_failed_result(registry):
    import asyncio

    class ExplodingLLM(LLMProvider):
        name = "boom"

        async def chat(self, messages, tools=None, agent="default", temperature=0.2):
            raise ConnectionError("网络断了")

    agent = BaseAgent(role="t", system_prompt="s", allowed_tools=[],
                      llm=ExplodingLLM(), tools=registry, bus=EventBus(),
                      ctx=ContextManager(), task_id="T")
    result = asyncio.run(agent.run("q", ""))
    assert not result.success and "网络断了" in result.output
