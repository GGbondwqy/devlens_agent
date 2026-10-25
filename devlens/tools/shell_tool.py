"""受控命令执行工具：白名单 + 超时 + 拒绝记录。

给排障类角色（如 CI 排查）执行 git / pytest 的能力；
一切越权命令会被沙箱拦截并记录（可在 denied_commands 里审计）。
"""

from __future__ import annotations

from typing import Any

from devlens.tools.base import Tool, ToolResult
from devlens.tools.sandbox import Sandbox


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "在工作区内执行研发命令（白名单：git / python / pytest）。"
        "例如 git status、git show、python -m pytest tests/ -x。"
        "其他命令会被沙箱拒绝。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
        },
        "required": ["command"],
    }
    requires_approval = True

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        cmd = str(kwargs["command"])
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.sandbox.run_command(cmd))
        # 已在事件循环内：同步桥接（用独立线程跑，避免嵌套 loop）
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.sandbox.run_command(cmd)).result()
