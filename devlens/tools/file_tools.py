"""文件类工具：读 / 写 / 列目录 / 正则代码搜索。

所有路径都经沙箱 resolve_in() 收敛到工作区内；输出带行号（ACI：
行号让模型能直接产出 file:line 形式的证据引用）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from devlens.tools.base import Tool, ToolResult
from devlens.tools.sandbox import Sandbox

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".runs"}
_MAX_SEARCH_HITS = 50


def _looks_binary(chunk: bytes) -> bool:
    return b"\x00" in chunk


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "读取工作区内文本文件的内容，返回带行号的文本。"
        "大文件建议用 start_line/end_line 分段读取。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "start_line": {"type": "integer", "description": "起始行（1-based，可选）"},
            "end_line": {"type": "integer", "description": "结束行（含，可选）"},
        },
        "required": ["path"],
    }

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        path = self.sandbox.resolve_in(str(kwargs["path"]))
        if not path.exists() or not path.is_file():
            return ToolResult(ok=False, error=f"文件不存在: {kwargs['path']}")
        raw = path.read_bytes()
        if _looks_binary(raw[:8192]):
            return ToolResult(ok=False, error="疑似二进制文件，不支持读取")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        start = max(1, int(kwargs.get("start_line") or 1))
        end = min(len(lines), int(kwargs.get("end_line") or len(lines)))
        if start > len(lines):
            return ToolResult(ok=False, error=f"start_line 超出文件行数({len(lines)})")
        body = "\n".join(f"{i:>4} | {lines[i - 1]}" for i in range(start, end + 1))
        header = f"{self.sandbox.relative_display(path)}（共 {len(lines)} 行，显示 {start}-{end} 行）"
        return ToolResult(ok=True, output=f"{header}\n{body}")


class WriteFileTool(Tool):
    name = "write_file"
    description = "把文本内容写入工作区内的文件（覆盖式）。写操作需要审批。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    }
    requires_approval = True

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        path = self.sandbox.resolve_in(str(kwargs["path"]))
        content = str(kwargs["content"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            ok=True,
            output=f"已写入 {self.sandbox.relative_display(path)}（{len(content)} 字符）",
            meta={"path": str(path)},
        )


class ListDirTool(Tool):
    name = "list_dir"
    description = "列出工作区内某个目录的内容（文件名、类型、大小）。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作区的目录路径，默认 ."},
        },
        "required": [],
    }

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        raw = str(kwargs.get("path") or ".")
        path = self.sandbox.resolve_in(raw)
        if not path.exists():
            return ToolResult(ok=False, error=f"目录不存在: {raw}")
        if path.is_file():
            path = path.parent
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for e in entries[:200]:
            if e.name in _SKIP_DIRS:
                continue
            kind = "dir " if e.is_dir() else "file"
            size = e.stat().st_size if e.is_file() else ""
            lines.append(f"{kind} {e.name} {size}")
        return ToolResult(ok=True, output=f"{self.sandbox.relative_display(path)}:\n" + "\n".join(lines))


class SearchCodeTool(Tool):
    """正则代码搜索（grep 的安全子集）。

    说明：真实模型场景下它与「结构感知检索」互补——精确文本匹配快而准，
    结构检索（见 kb/AST）负责理解符号关系。此处先提供 grep 能力。
    """

    name = "search_code"
    description = (
        "在工作区内用正则表达式搜索代码，返回 path:line:内容 形式的命中列表。"
        "适合查找函数名、报错文案、标识符。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "glob": {"type": "string", "description": "文件名过滤，如 *.py（可选）"},
        },
        "required": ["pattern"],
    }

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(self, **kwargs: Any) -> ToolResult:
        pattern = str(kwargs["pattern"])
        glob = str(kwargs.get("glob") or "*")
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return ToolResult(ok=False, error=f"非法正则: {exc}")

        hits: list[str] = []
        scanned = 0
        for path in self.sandbox.workspace.rglob(glob):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            scanned += 1
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if _looks_binary(raw[:8192]):
                continue
            display = self.sandbox.relative_display(path)
            for lineno, line in enumerate(
                raw.decode("utf-8", errors="replace").splitlines(), start=1
            ):
                if rx.search(line):
                    hits.append(f"{display}:{lineno}: {line.strip()[:200]}")
                    if len(hits) >= _MAX_SEARCH_HITS:
                        break
            if len(hits) >= _MAX_SEARCH_HITS:
                break

        if not hits:
            return ToolResult(ok=True, output=f"无命中（已扫描 {scanned} 个文件）")
        note = f"\n…[仅展示前 {_MAX_SEARCH_HITS} 条]" if len(hits) >= _MAX_SEARCH_HITS else ""
        return ToolResult(ok=True, output="\n".join(hits) + note, meta={"scanned": scanned})
