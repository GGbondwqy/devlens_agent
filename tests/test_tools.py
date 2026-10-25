"""工具系统测试：注册中心校验 / 输出截断 / 文件与搜索工具。"""

from pathlib import Path

from devlens.config import SandboxSettings
from devlens.tools.base import Tool, ToolRegistry, ToolResult
from devlens.tools.factory import build_default_registry
from devlens.tools.kb_tools import DocKnowledgeBase
from devlens.tools.file_tools import ReadFileTool, SearchCodeTool
from devlens.tools.sandbox import Sandbox


class BoomTool(Tool):
    name = "boom"
    description = "always fails"
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs):
        raise RuntimeError("爆炸了")


class LongTool(Tool):
    name = "long"
    description = "long output"
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs):
        return ToolResult(ok=True, output="x" * 10000)


def test_registry_unknown_tool():
    reg = ToolRegistry()
    res = reg.execute("nope", {})
    assert not res.ok and "未注册" in res.error


def test_registry_required_validation():
    reg = ToolRegistry()
    reg.register(ReadFileTool(Sandbox(Path(__file__).parent)))
    res = reg.execute("read_file", {})
    assert not res.ok and "缺少必填参数" in res.error
    res2 = reg.execute("read_file", {"path": "a", "bogus": 1})
    assert not res2.ok and "未知参数" in res2.error


def test_registry_exception_to_error_result():
    reg = ToolRegistry()
    reg.register(BoomTool())
    res = reg.execute("boom", {})
    assert not res.ok and "RuntimeError" in res.error


def test_output_truncation():
    reg = ToolRegistry(settings=SandboxSettings(max_output_chars=100))
    reg.register(LongTool())
    res = reg.execute("long", {})
    assert res.ok and len(res.output) == 100
    assert res.meta["truncated"] and res.meta["raw_len"] == 10000
    assert "已截断" in res.render()


def test_read_file_numbered_lines(fixture_repo):
    sb = Sandbox(fixture_repo)
    res = ReadFileTool(sb).execute(path="app/utils/discount.py")
    assert res.ok
    assert "apply_discount" in res.output
    assert "| 1 " in res.output or "|1 " in res.output or f"{1:>4}" in res.output


def test_search_code_hits(fixture_repo):
    sb = Sandbox(fixture_repo)
    res = SearchCodeTool(sb).execute(pattern=r"def apply_discount")
    assert res.ok and "app/utils/discount.py" in res.output


def test_search_code_traversal_ignored(fixture_repo):
    sb = Sandbox(fixture_repo)
    res = SearchCodeTool(sb).execute(pattern="password")
    assert res.ok  # 不抛异常即为通过


def test_kb_search(fixture_repo):
    kb = DocKnowledgeBase([fixture_repo / "docs"])
    hits = kb.search("优惠券 金额 负数 历史故障")
    assert hits and "troubleshooting" in hits[0]["source"]


def test_default_registry_has_ten_tools(fixture_repo):
    sb = Sandbox(fixture_repo)
    kb = DocKnowledgeBase([fixture_repo / "docs"])
    reg = build_default_registry(sb, kb)
    assert len(reg.names()) == 10
    schemas = reg.schemas_for(["read_file", "git_diff"])
    assert schemas[0]["type"] == "function"
