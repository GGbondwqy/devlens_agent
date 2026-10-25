"""LLM 抽象层：多 Provider 统一接口（多模型网关的基座）。

消息与响应模型对齐 OpenAI Chat Completions 语义（system/user/assistant/tool +
tool_calls），因此任何兼容端点（DeepSeek / Qwen / GLM / vLLM 本地模型）都能直接接。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """一次工具调用请求（由模型产出）。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def text_for_context(self) -> str:
        """用于 token 估算与摘要的纯文本视图。"""
        parts: list[str] = []
        if self.content:
            parts.append(self.content)
        for tc in self.tool_calls or []:
            parts.append(f"{tc.name} {tc.arguments}")
        return "\n".join(parts)


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"  # stop | tool_calls | length
    usage: Usage = Field(default_factory=Usage)
    model: str = "mock"


class LLMProvider(ABC):
    """所有模型后端的统一接口。

    实现方约定：
        * chat() 必须无副作用（不修改传入 messages）；
        * tools 传入 OpenAI function calling 格式的 schema 列表；
        * agent 是调用方角色名，供 Mock 策略与多模型路由使用。
    """

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        agent: str = "default",
        temperature: float = 0.2,
    ) -> LLMResponse: ...


def tool_call_id_seq(prefix: str = "call") -> Any:
    """生成递增的工具调用 id（mock 场景使用）。"""
    counter = {"n": 0}

    def next_id() -> str:
        counter["n"] += 1
        return f"{prefix}_{counter['n']:04d}"

    return next_id
