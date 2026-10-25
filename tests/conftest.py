"""pytest 共享夹具。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def fixture_repo():
    """确保靶场仓库已初始化（git main + pr-42）。"""
    spec = importlib.util.spec_from_file_location(
        "setup_fixtures", PROJECT_ROOT / "scripts" / "setup_fixtures.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ensure_fixture_repo(force=False)
    return PROJECT_ROOT / "fixtures" / "repo"
