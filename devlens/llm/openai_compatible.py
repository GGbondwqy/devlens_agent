"""OpenAI 兼容模型后端（预留实现）。

支持任何 OpenAI Chat Completions 兼容端点：DeepSeek / Qwen(DashScope 兼容模式)
/ GLM / Kimi / vLLM / Ollama 等。消息与工具协议与本框架的抽象层一一对应。

注意：当前仓库处于「框架先行」阶段，此实现未在真实 Key 下联调过；
字段序列化按官方 API 规范编写，接模型时如有出入只需微调本文件。
"""

from __future__ import annotations

from typing import Any

import httpx

from devlens.llm.base import ChatMessage, LLMProvider, LLMResponse, ToolCall, Usage


class OpenAICompatibleLLM(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_map: dict[str, str] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_map = model_map or {"default": "deepseek-chat"}
        self.timeout = timeout

    def _model_for(self, agent: str) -> str:
        return self.model_map.get(agent) or self.model_map.get("default") or "deepseek-chat"

    @staticmethod
    def _serialize(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": _dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            if m.role == "tool":
                msg["tool_call_id"] = m.tool_call_id or ""
            out.append(msg)
        return out

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        agent: str = "default",
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model_for(agent),
            "messages": self._serialize(messages),
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice.get("message", {})
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments=_loads(tc.get("function", {}).get("arguments") or "{}"),
            )
            for tc in message.get("tool_calls") or []
        ]
        usage = data.get("usage") or {}
        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else choice.get("finish_reason", "stop"),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            ),
            model=data.get("model", self._model_for(agent)),
        )


def _dumps(args: dict[str, Any]) -> str:
    import json

    return json.dumps(args, ensure_ascii=False)


def _loads(raw: str) -> dict[str, Any]:
    import json

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
