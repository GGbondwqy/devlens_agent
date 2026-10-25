"""工具系统基座：声明式工具 + 注册中心 + 统一执行入口。

设计要点（源自调研中对 ACI / Agent-Computer Interface 的结论）：
    1. 工具用 JSON Schema 声明参数——与 function calling 协议同构，
       将来接真实模型零改造；
    2. 统一走 ToolRegistry.execute()：参数校验、白名单、沙箱、输出截断
       全部收敛在一处，工具实现本身保持纯粹；
    3. 工具输出一律截断到 max_output_chars——工具是给模型看的接口，
       超长输出只会污染上下文（SWE-agent 的核心教训）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from devlens.config import SandboxSettings


class ToolResult(BaseModel):
    ok: bool = True
    output: str = ""
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def render(self) -> str:
        """渲染为回填给模型的消息文本。"""
        if not self.ok:
            return f"[工具执行失败] {self.error}"
        text = self.output
        if self.meta.get("truncated"):
            text += f"\n…[输出已截断，原始长度 {self.meta['raw_len']} 字符]"
        return text or "(空结果)"


class Tool(ABC):
    """工具基类：子类声明 name / description / parameters 并实现 execute。"""

    name: str = "tool"
    description: str = ""
    # JSON Schema：{"type":"object","properties":{...},"required":[...]}
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    # 写操作标记：接真实模型后可用于人工审批（Claude Code 的 PreToolUse hook 思路）
    requires_approval: bool = False

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult: ...

    def validate_args(self, args: dict[str, Any]) -> str | None:
        """粗粒度参数校验：required 缺失即拒绝。类型细节由各工具自行检查。"""
        for key in self.parameters.get("required", []):
            if key not in args or args[key] in (None, ""):
                return f"缺少必填参数: {key}"
        props = self.parameters.get("properties", {})
        for key, value in args.items():
            if key not in props:
                return f"未知参数: {key}"
        return None

    def openai_schema(self) -> dict[str, Any]:
        """导出为 OpenAI function calling 格式（MCP/其他协议可另行适配）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册中心：注册、查询、schema 导出、统一执行。"""

    def __init__(self, settings: SandboxSettings | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.settings = settings or SandboxSettings()

    # ---- 注册 ----
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas_for(self, names: list[str]) -> list[dict[str, Any]]:
        return [self._tools[n].openai_schema() for n in names if n in self._tools]

    # ---- 执行 ----
    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """统一执行入口：未注册拦截 -> 参数校验 -> 截断包装。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"未注册的工具: {name}")
        args = args or {}
        err = tool.validate_args(args)
        if err:
            return ToolResult(ok=False, error=f"参数校验失败: {err}")
        try:
            result = tool.execute(**args)
        except Exception as exc:  # noqa: BLE001 工具异常转结构化错误，不炸 Agent 循环
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        limit = self.settings.max_output_chars
        if result.ok and len(result.output) > limit:
            result.meta["truncated"] = True
            result.meta["raw_len"] = len(result.output)
            result.output = result.output[:limit]
        return result
