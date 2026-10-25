"""Agent 内核：ReAct 循环（Think -> Act -> Observe -> ... -> Final Answer）。

这是整个框架的心脏，对应调研结论中的共性规律——主流实现的内核都收敛为
「while 循环 + 工具调用回填消息列表」：

    while 步数未用尽:
        resp = llm.chat(messages, tools)          # Think
        if resp 有工具调用:
            for call in resp.tool_calls:          # Act
                result = tools.execute(call)      # 沙箱内执行
                messages.append(tool result)      # Observe
            continue
        return resp.content                       # Final Answer

内核本身不知道任何业务（研发场景知识都在 roles/ 与 workflow/），
也不知道具体模型（LLM 由 Provider 抽象注入）——因此 Mock 与真实模型
可以无缝互换。
"""

from __future__ import annotations

from pydantic import BaseModel

from devlens.core.context import ContextManager
from devlens.core.events import Event, EventBus, EventType
from devlens.llm.base import ChatMessage, LLMProvider, ToolCall
from devlens.tools.base import ToolRegistry


class AgentResult(BaseModel):
    role: str
    output: str = ""
    success: bool = True
    stopped_reason: str = "completed"  # completed | max_steps | error
    steps: int = 0
    tool_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class BaseAgent:
    """一个可复用的角色化 Agent。业务差异全部由 RoleSpec 声明。"""

    def __init__(
        self,
        role: str,
        system_prompt: str,
        allowed_tools: list[str],
        llm: LLMProvider,
        tools: ToolRegistry,
        bus: EventBus,
        ctx: ContextManager,
        max_steps: int = 8,
        task_id: str = "",
    ) -> None:
        self.role = role
        self.system_prompt = system_prompt
        self.allowed_tools = allowed_tools
        self.llm = llm
        self.tools = tools
        self.bus = bus
        self.ctx = ctx
        self.max_steps = max_steps
        self.task_id = task_id

    # ---- 事件 ----
    def _emit(self, type_: EventType, message: str, **data) -> None:
        self.bus.emit(
            Event(
                type=type_,
                task_id=self.task_id,
                node=data.pop("node", None),
                agent=self.role,
                message=message,
                data=data,
            )
        )

    # ---- 主循环 ----
    async def run(self, task: str, background: str = "") -> AgentResult:
        user_text = task if not background else f"{task}\n\n[上游上下文]\n{background}"
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt),
            ChatMessage(role="user", content=user_text),
        ]
        schemas = self.tools.schemas_for(self.allowed_tools)
        result = AgentResult(role=self.role)

        for step in range(1, self.max_steps + 1):
            result.steps = step
            try:
                resp = await self.llm.chat(
                    messages, tools=schemas, agent=self.role
                )
            except Exception as exc:  # noqa: BLE001 模型层异常不应击穿任务
                result.success = False
                result.stopped_reason = "error"
                result.output = f"LLM 调用失败: {exc}"
                self._emit(EventType.TASK_FAILED, result.output)
                return result

            result.prompt_tokens += resp.usage.prompt_tokens
            result.completion_tokens += resp.usage.completion_tokens

            if not resp.tool_calls:
                result.output = resp.content or ""
                self._emit(EventType.AGENT_THOUGHT, result.output)
                return result

            # 有思考文本也记录（中间推理）
            if resp.content:
                self._emit(EventType.AGENT_THOUGHT, resp.content)

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=resp.content,
                    tool_calls=list(resp.tool_calls),
                )
            )
            for call in resp.tool_calls:
                result.tool_call_count += 1
                self._emit(
                    EventType.TOOL_CALLED,
                    f"{call.name}({', '.join(f'{k}={v!r}' for k, v in list(call.arguments.items())[:3])})",
                    tool=call.name,
                    arguments=call.arguments,
                )
                tool_result = self.tools.execute(call.name, call.arguments)
                self._emit(
                    EventType.TOOL_RESULT,
                    tool_result.render()[:300],
                    tool=call.name,
                    ok=tool_result.ok,
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=tool_result.render(),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

            messages = self.ctx.trim(messages)

        result.success = False
        result.stopped_reason = "max_steps"
        result.output = f"[{self.role}] 达到最大步数限制（{self.max_steps}），强制返回当前结论。"
        self._emit(EventType.AGENT_THOUGHT, result.output)
        return result
