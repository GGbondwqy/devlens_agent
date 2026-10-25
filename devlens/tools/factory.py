"""工具集工厂：按场景组装默认工具注册表。"""

from __future__ import annotations

from devlens.tools.base import ToolRegistry
from devlens.tools.file_tools import (
    ListDirTool,
    ReadFileTool,
    SearchCodeTool,
    WriteFileTool,
)
from devlens.tools.git_tools import GitBranchTool, GitDiffTool, GitLogTool
from devlens.tools.kb_tools import DocKnowledgeBase, SearchDocsTool
from devlens.tools.log_tools import ReadLogTool
from devlens.tools.shell_tool import RunCommandTool
from devlens.tools.sandbox import Sandbox


def build_default_registry(sandbox: Sandbox, kb: DocKnowledgeBase) -> ToolRegistry:
    registry = ToolRegistry(settings=sandbox.settings)
    registry.register_all(
        [
            ReadFileTool(sandbox),
            WriteFileTool(sandbox),
            ListDirTool(sandbox),
            SearchCodeTool(sandbox),
            GitDiffTool(sandbox),
            GitLogTool(sandbox),
            GitBranchTool(sandbox),
            ReadLogTool(sandbox),
            SearchDocsTool(kb),
            RunCommandTool(sandbox),
        ]
    )
    return registry
