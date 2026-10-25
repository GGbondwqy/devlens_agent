"""上下文管理：给 Agent 循环加一层 token 预算保护。

调研结论（对齐主流实现）：
    * Claude Code 用 prompt caching + PreCompact 压缩钩子；
    * OpenHands 用可插拔的 Condenser 管道压缩对话历史；
    * SWE-agent 用 history_processors 管道处理历史消息。

本框架实现轻量版：超预算时保留 system 提示与最近 N 条消息，
被裁剪的中段替换为一条「摘要占位」消息。摘要函数可插拔，
接入真实模型后可替换为 LLM 摘要（ hierarchical summarization ）。
"""

from __future__ import annotations

from devlens.llm.base import ChatMessage


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字符按 1 token/字，其余按 4 字符/token。

    不追求精确，只用于预算控制（与真实 tokenizer 的偏差在 20% 量级即可）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4 + 1


class ContextManager:
    def __init__(self, max_tokens: int = 8000, keep_recent: int = 8) -> None:
        self.max_tokens = max_tokens
        self.keep_recent = keep_recent

    def total_tokens(self, messages: list[ChatMessage]) -> int:
        return sum(estimate_tokens(m.text_for_context()) for m in messages)

    def trim(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """超过预算时裁剪中段历史。

        策略：
            1. system 消息永远保留（Agent 人设与规则不能丢）；
            2. 最近 keep_recent 条消息保留（当前工作状态）；
            3. 中段消息压缩为一条摘要占位，保留工具调用的关键痕迹。
        """
        if self.total_tokens(messages) <= self.max_tokens:
            return messages

        system_idx = {i for i, m in enumerate(messages) if m.role == "system"}
        first_user = next((i for i, m in enumerate(messages) if m.role == "user"), None)
        keep_from = max(0, len(messages) - self.keep_recent)

        # 中段 = 非 system、非首条 user、且早于保留窗口的消息
        middle_idx = {
            i
            for i in range(len(messages))
            if i not in system_idx and i != first_user and i < keep_from
        }
        if not middle_idx:
            return messages

        middle = [messages[i] for i in sorted(middle_idx)]
        summary_text = self.summarize(middle)
        # 保底：摘要必须比被替换内容更小，否则退化为计数占位
        if estimate_tokens(summary_text) >= sum(
            estimate_tokens(m.text_for_context()) for m in middle
        ):
            summary_text = f"[历史上下文已压缩：{len(middle)} 条消息被裁剪（工具调用与结果已省略）]"
        summary_msg = ChatMessage(role="user", content=summary_text)
        result: list[ChatMessage] = []
        inserted = False
        for i, m in enumerate(messages):
            if i in middle_idx:
                if not inserted:
                    result.append(summary_msg)
                    inserted = True
            else:
                result.append(m)
        return result

    def summarize(self, messages: list[ChatMessage]) -> str:
        """抽取式摘要：保留关键痕迹（工具调用与结果首行），压缩自然语言。"""
        lines: list[str] = ["[历史上下文已压缩，以下为被裁剪消息的摘要]"]
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    args = str(tc.arguments)[:120]
                    lines.append(f"- 调用工具 {tc.name}({args})")
            elif m.role == "tool" and m.content:
                first_line = m.content.strip().splitlines()[0][:100]
                lines.append(f"- 工具返回: {first_line}")
            elif m.role == "assistant" and m.content:
                lines.append(f"- 助手输出: {m.content[:100]}")
        return "\n".join(lines[:40])
