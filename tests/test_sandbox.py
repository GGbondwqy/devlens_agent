"""沙箱安全边界测试。"""

import asyncio

import pytest

from devlens.config import SandboxSettings
from devlens.tools.sandbox import Sandbox


def test_path_traversal_blocked(tmp_path):
    sb = Sandbox(tmp_path)
    with pytest.raises(PermissionError):
        sb.resolve_in("../../etc/passwd")
    with pytest.raises(PermissionError):
        sb.resolve_in("/c/Windows/system32")


def test_relative_path_resolves_in_workspace(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    sb = Sandbox(tmp_path)
    assert sb.resolve_in("a.txt") == (tmp_path / "a.txt").resolve()


def test_relative_path_resolves_in_extra_root(tmp_path, tmp_path_factory):
    logs = tmp_path_factory.mktemp("logs")
    log = logs / "ci.log"
    log.write_text("x", encoding="utf-8")
    sb = Sandbox(tmp_path, extra_roots=[logs])
    assert sb.resolve_in("ci.log") == log.resolve()
    # 相对路径带上根目录名也能解析（项目根视角）
    assert sb.resolve_in(str(log)) == log.resolve()


def test_command_whitelist(tmp_path):
    sb = Sandbox(tmp_path, settings=SandboxSettings(command_whitelist=["git", "python"]))
    assert sb.check_command("git status") is None
    assert sb.check_command("python -m pytest -q") is None
    assert sb.check_command("rm -rf /") is not None
    assert sb.check_command("curl http://evil.com") is not None
    assert sb.check_command("") is not None


def test_run_command_blocked_and_allowed(tmp_path):
    sb = Sandbox(tmp_path, settings=SandboxSettings(command_whitelist=["python"], command_timeout=10))
    blocked = asyncio.run(sb.run_command("del *.*"))
    assert not blocked.ok and "白名单" in blocked.error
    ok = asyncio.run(sb.run_command('python -c "print(1+1)"'))
    assert ok.ok and "2" in ok.output
    assert len(sb.denied_commands) == 1
