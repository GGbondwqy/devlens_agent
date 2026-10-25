"""多模型网关：按角色路由模型。

* mock 模式：每个角色绑定一个确定性策略（见 mock_policies.build_policy）；
* 真实模式：每个角色可配置不同模型（如 critic 用更强/更便宜的模型），
  这是多模型成本优化的落点（调研中 OpenHands 的 LLM 路由同理）。
"""

from __future__ import annotations

from typing import Any

from devlens.config import Settings
from devlens.llm.base import LLMProvider
from devlens.llm.mock import MockLLMProvider, MockPolicy
from devlens.llm.mock_policies import EchoPolicy, build_policy
from devlens.llm.openai_compatible import OpenAICompatibleLLM


class LLMRouter:
    def __init__(
        self,
        settings: Settings,
        scenario: str,
        task_input: dict[str, Any],
    ) -> None:
        self.settings = settings
        self.scenario = scenario
        self.task_input = task_input
        self._cache: dict[str, LLMProvider] = {}

    def for_role(self, role: str) -> LLMProvider:
        if role in self._cache:
            return self._cache[role]

        if self.settings.llm.provider == "mock":
            policy: MockPolicy = build_policy(role, self.scenario, self.task_input)
            provider: LLMProvider = MockLLMProvider(policy)
        else:
            cfg = self.settings.llm.openai_compatible
            provider = OpenAICompatibleLLM(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                model_map=cfg.model_map,
            )
        self._cache[role] = provider
        return provider


__all__ = ["LLMRouter", "EchoPolicy"]
