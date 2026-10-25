"""上下文管理测试。"""

from devlens.core.context import ContextManager, estimate_tokens
from devlens.llm.base import ChatMessage, ToolCall


def test_estimate_tokens_basic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") >= 1
    assert estimate_tokens("你好世界") >= 4  # CJK 按字计


def test_trim_under_budget_noop():
    cm = ContextManager(max_tokens=10000)
    msgs = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="hi")]
    assert cm.trim(msgs) == msgs


def test_trim_keeps_system_and_first_user():
    cm = ContextManager(max_tokens=200, keep_recent=2)
    msgs = [
        ChatMessage(role="system", content="系统提示" * 50),
        ChatMessage(role="user", content="原始任务" * 50),
    ]
    for i in range(10):
        msgs.append(
            ChatMessage(
                role="assistant",
                tool_calls=[ToolCall(id=f"c{i}", name="read_file", arguments={"path": f"f{i}.py"})],
            )
        )
        msgs.append(ChatMessage(role="tool", content=f"工具结果 {i} " + "x" * 100, tool_call_id=f"c{i}"))

    trimmed = cm.trim(msgs)
    assert trimmed[0].role == "system"
    assert any(m.role == "user" and "原始任务" in (m.content or "") for m in trimmed)
    # 中段被压缩为摘要占位
    assert any("已压缩" in (m.content or "") for m in trimmed)
    # 最近的工具结果仍在
    assert any(m.role == "tool" and "工具结果 9" in (m.content or "") for m in trimmed)
    assert cm.total_tokens(trimmed) < cm.total_tokens(msgs)


def test_summarize_preserves_tool_trace():
    cm = ContextManager()
    msgs = [
        ChatMessage(role="assistant", tool_calls=[ToolCall(id="c", name="search_code", arguments={"pattern": "x"})]),
        ChatMessage(role="tool", content="app/a.py:1: hello", tool_call_id="c"),
    ]
    summary = cm.summarize(msgs)
    assert "search_code" in summary and "app/a.py" in summary
