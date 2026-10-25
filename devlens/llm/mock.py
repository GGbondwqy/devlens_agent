"""Mock 模型后端：让全流程在无 API Key 的环境下完整跑通。

定位：
    * MockLLMProvider 只是一个「管道」，把对话历史交给注入的策略（MockPolicy）；
    * 策略按角色实现确定性决策（工具调用序列 / 最终结论），
      模拟真实模型的行为轨迹，用于：
        - 框架演示与端到端测试（CI 不花钱、不抖动）；
        - 验证工具系统 / 上下文管理 / 编排引擎 / 事件流的正确性。
    * 切换真实模型 = 配置 provider: openai_compatible，其余代码零改动。
"""

from __future__ import annotations

from typing import Any, Protocol

from devlens.llm.base import ChatMessage, LLMProvider, LLMResponse


class MockPolicy(Protocol):
    """策略接口：给定对话历史，返回模型响应（工具调用或最终结论）。"""

    async def respond(
        self,
        agent: str,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
    ) -> LLMResponse: ...


class MockLLMProvider(LLMProvider):
    name = "mock"

    def __init__(self, policy: MockPolicy) -> None:
        self.policy = policy

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        agent: str = "default",
        temperature: float = 0.2,
    ) -> LLMResponse:
        resp = await self.policy.respond(agent, messages, tools)
        # 模型计费口径：粗估 token，供统计面板展示
        if resp.usage.total_tokens == 0:
            prompt = sum(len(m.text_for_context()) for m in messages) // 3
            completion = len((resp.content or "")) // 3 + 20 * len(resp.tool_calls)
            resp.usage = resp.usage.__class__(prompt_tokens=prompt, completion_tokens=completion)
        return resp
