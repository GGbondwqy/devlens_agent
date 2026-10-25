"""日志工具：读取 CI / 服务日志（路径在沙箱的只读附加根内）。"""

from __future__ import annotations

import re
from typing import Any

from devlens.tools.base import Tool, ToolResult
from devlens.tools.sandbox import Sandbox


class ReadLogTool(Tool):
    name = "read_log"
    description = (
        "读取日志文件（CI 流水线日志、服务运行日志）。支持 tail 模式与"
        "关键字过滤，返回带行号的日志片段。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "日志文件路径"},
            "tail": {"type": "integer", "description": "只看最后 N 行，默认 120"},
            "filter": {"type": "string", "description": "关键字/正则过滤（可选）"},
        },
        "required": ["path"],
    }

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        path = self.sandbox.resolve_in(str(kwargs["path"]))
        if not path.exists() or not path.is_file():
            return ToolResult(ok=False, error=f"日志不存在: {kwargs['path']}")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        indexed = list(enumerate(lines, start=1))
        flt = kwargs.get("filter")
        if flt:
            rx = re.compile(str(flt), re.IGNORECASE)
            indexed = [(i, ln) for i, ln in indexed if rx.search(ln)]
        tail = int(kwargs.get("tail") or 120)
        shown = indexed[-tail:]
        body = "\n".join(f"{i:>4} | {ln}" for i, ln in shown[:200]) or "（无命中行）"
        head = f"{kwargs['path']}（共 {len(lines)} 行，命中 {len(indexed)} 行，显示 {len(shown)} 行）"
        return ToolResult(ok=True, output=f"{head}\n{body}")
