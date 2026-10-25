"""全局配置加载。

优先级：环境变量 > config/settings.yaml > 代码内默认值。
配置文件缺失时使用默认值，保证框架零配置可跑。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


class OpenAICompatibleSettings(BaseModel):
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model_map: dict[str, str] = Field(default_factory=lambda: {"default": "deepseek-chat"})


class LLMSettings(BaseModel):
    provider: str = "mock"  # mock | openai_compatible
    openai_compatible: OpenAICompatibleSettings = Field(default_factory=OpenAICompatibleSettings)
    temperature: float = 0.2


class ContextSettings(BaseModel):
    max_tokens: int = 8000
    keep_recent_messages: int = 8


class SandboxSettings(BaseModel):
    command_whitelist: list[str] = Field(default_factory=lambda: ["git", "python", "pytest"])
    command_timeout: int = 30
    max_output_chars: int = 6000


class WorkflowSettings(BaseModel):
    max_rounds: int = 12
    max_reflections: int = 1


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class StorageSettings(BaseModel):
    runs_dir: str = ".runs"


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """环境变量覆盖：DELENS_API_KEY / DELENS_LLM_PROVIDER / DELENS_BASE_URL。"""
    llm = data.setdefault("llm", {})
    if os.environ.get("DELENS_LLM_PROVIDER"):
        llm["provider"] = os.environ["DELENS_LLM_PROVIDER"]
    if os.environ.get("DELENS_BASE_URL"):
        llm.setdefault("openai_compatible", {})["base_url"] = os.environ["DELENS_BASE_URL"]
    if os.environ.get("DELENS_API_KEY"):
        llm.setdefault("openai_compatible", {})["api_key"] = os.environ["DELENS_API_KEY"]
    return data


def load_settings(config_path: str | Path | None = None) -> Settings:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = _apply_env_overrides(data)
    return Settings.model_validate(data)


# 项目根目录（devlens-agent/），供 CLI / server / fixtures 定位使用
PROJECT_ROOT = _PROJECT_ROOT
