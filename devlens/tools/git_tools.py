"""Git 工具：基于真实 git 命令（经沙箱白名单执行）。

PR 评审场景的底座：git_diff 展示分支差异，git_log 提供变更历史上下文。
"""

from __future__ import annotations

from typing import Any

from devlens.tools.base import Tool, ToolResult
from devlens.tools.sandbox import Sandbox


class GitDiffTool(Tool):
    name = "git_diff"
    description = (
        "查看工作区 git 仓库的代码差异。指定 base 时展示当前 HEAD 相对 base 的差异"
        "（PR 评审用），否则展示未提交改动。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "base": {"type": "string", "description": "基准分支/commit，如 main（可选）"},
        },
        "required": [],
    }

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        base = kwargs.get("base")
        cmd = f"git diff {base}" if base else "git diff HEAD"
        result = _run(self.sandbox, cmd)
        if result.ok and not result.output.strip():
            result.output = f"与 {base or 'HEAD'} 无差异"
        return result


class GitLogTool(Tool):
    name = "git_log"
    description = "查看工作区 git 仓库的提交历史（默认最近 10 条，含哈希/作者/说明）。"
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "条数，默认 10"},
        },
        "required": [],
    }

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        limit = int(kwargs.get("limit") or 10)
        return _run(self.sandbox, f'git log --oneline -n {limit} --date=short --pretty=format:"%h %ad %an %s"')


class GitBranchTool(Tool):
    name = "git_branch"
    description = "列出工作区 git 仓库的本地分支（当前分支带 * 标记）。"
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        return _run(self.sandbox, "git branch")


def _run(sandbox: Sandbox, cmd: str) -> ToolResult:
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # 在已运行的事件循环里被同步调用时（少见），退化为阻塞执行
        import subprocess

        proc = subprocess.run(
            cmd, cwd=str(sandbox.workspace), shell=True, capture_output=True, timeout=sandbox.settings.command_timeout
        )
        text = proc.stdout.decode("utf-8", errors="replace") + proc.stderr.decode("utf-8", errors="replace")
        return ToolResult(ok=proc.returncode == 0, output=text, error=None if proc.returncode == 0 else f"退出码 {proc.returncode}")
    return asyncio.run(sandbox.run_command(cmd))
