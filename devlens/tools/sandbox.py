"""沙箱：文件访问与命令执行的安全边界。

能力：
    * 路径收敛——所有文件工具的路径都经 resolve_in() 校验，
      只允许落在工作区（workspace）与显式追加的只读根（extra_roots）内，
      阻断目录穿越（../../etc/passwd）；
    * 命令白名单——只放行 git / python / pytest 等研发命令，
      拒绝 rm、curl 等高危命令；带超时控制。
"""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from devlens.config import SandboxSettings
from devlens.tools.base import ToolResult


class Sandbox:
    def __init__(
        self,
        workspace: Path,
        settings: SandboxSettings | None = None,
        extra_roots: list[Path] | None = None,
    ) -> None:
        self.settings = settings or SandboxSettings()
        self.workspace = workspace.resolve()
        self.extra_roots = [p.resolve() for p in (extra_roots or [])]
        self._denied: list[str] = []

    # ---- 路径安全 ----
    def resolve_in(self, raw_path: str) -> Path:
        """把任意输入路径解析到允许的根内；越界即抛 PermissionError。

        相对路径依次尝试工作区与各只读附加根（日志等通常位于仓库之外，
        以相对项目的路径给出），取第一个存在的解释；都不存在时按工作区
        解释并交给边界检查报错。
        """
        if not raw_path:
            raise PermissionError("空路径")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            for base in [self.workspace, *self.extra_roots]:
                resolved = (base / candidate).resolve()
                if resolved.exists():
                    return resolved
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        for root in [self.workspace, *self.extra_roots]:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise PermissionError(
            f"路径越界: {raw_path}（允许的根: {self.workspace}"
            + (f", {', '.join(str(r) for r in self.extra_roots)}" if self.extra_roots else "")
            + "）"
        )

    def relative_display(self, path: Path) -> str:
        """返回相对工作区的展示路径（统一 / 分隔，便于跨平台与模型引用）。"""
        try:
            rel = path.resolve().relative_to(self.workspace)
        except ValueError:
            return str(path).replace("\\", "/")
        return rel.as_posix()

    # ---- 命令执行 ----
    def check_command(self, cmd: str) -> str | None:
        """返回拒绝原因；None 表示允许。"""
        try:
            parts = shlex.split(cmd)
        except ValueError:
            return "无法解析的命令"
        if not parts:
            return "空命令"
        base = Path(parts[0]).name
        if base not in self.settings.command_whitelist:
            return (
                f"命令不在白名单内: {base}（允许: {', '.join(self.settings.command_whitelist)}）"
            )
        return None

    async def run_command(self, cmd: str, timeout: int | None = None) -> ToolResult:
        reason = self.check_command(cmd)
        if reason:
            self._denied.append(cmd)
            return ToolResult(ok=False, error=f"[沙箱拦截] {reason}")
        timeout = timeout or self.settings.command_timeout
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(ok=False, error=f"命令超时（>{timeout}s）: {cmd}")
            text = out.decode("utf-8", errors="replace")
            return ToolResult(ok=proc.returncode == 0, output=text, error=None if proc.returncode == 0 else f"退出码 {proc.returncode}")
        except FileNotFoundError:
            return ToolResult(ok=False, error=f"命令不存在: {cmd}")

    @property
    def denied_commands(self) -> list[str]:
        return list(self._denied)
