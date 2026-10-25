"""任务运行器：把配置 / 工具 / 模型路由 / 角色 / 流程图组装成一次可执行的任务。

这是框架对外的唯一编排入口——CLI 与 Web 服务都只调它。
"""

from __future__ import annotations

import time
from typing import Any

from devlens.config import PROJECT_ROOT, Settings, load_settings
from devlens.core.agent import BaseAgent
from devlens.core.context import ContextManager
from devlens.core.events import EventBus, EventSinker
from devlens.llm.router import LLMRouter
from devlens.memory.local import RunsStore
from devlens.roles.specs import ROLE_SPECS
from devlens.tools.factory import build_default_registry
from devlens.tools.kb_tools import DocKnowledgeBase
from devlens.tools.sandbox import Sandbox
from devlens.workflow.engine import FlowEngine, FlowState
from devlens.workflow.graphs import build_graph

SUPPORTED_SCENARIOS = ("issue", "pr", "ci")


class TaskRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.store = RunsStore(PROJECT_ROOT / self.settings.storage.runs_dir)

    async def run_task(
        self,
        scenario: str,
        data: dict[str, Any],
        task_id: str | None = None,
        resume: bool = False,
        bus: EventBus | None = None,
    ) -> FlowState:
        if scenario not in SUPPORTED_SCENARIOS:
            raise ValueError(f"未知场景: {scenario}（支持: {', '.join(SUPPORTED_SCENARIOS)}）")

        data = dict(data)
        tid = str(task_id or data.get("id") or f"{scenario}-{time.strftime('%Y%m%d-%H%M%S')}")
        data.setdefault("id", tid)

        bus = bus or EventBus()
        sinker = EventSinker(self.store.task_dir(tid))
        bus.subscribe_fn(sinker)

        try:
            repo_path = (PROJECT_ROOT / str(data.get("repo", "fixtures/repo"))).resolve()
            if not repo_path.exists():
                raise FileNotFoundError(f"仓库路径不存在: {repo_path}")

            sandbox = Sandbox(
                workspace=repo_path,
                settings=self.settings.sandbox,
                # 只读附加根：任务资料（Issue/PR/CI 日志等）以项目根相对路径给出
                extra_roots=[PROJECT_ROOT],
            )
            kb = DocKnowledgeBase([repo_path / "docs", PROJECT_ROOT / "fixtures" / "kb"])
            registry = build_default_registry(sandbox, kb)
            router = LLMRouter(self.settings, scenario, data)
            ctx = ContextManager(
                max_tokens=self.settings.context.max_tokens,
                keep_recent=self.settings.context.keep_recent_messages,
            )
            graph = build_graph(scenario, self.settings.workflow.max_reflections)
            agents = {
                spec.role: BaseAgent(
                    role=spec.role,
                    system_prompt=spec.system_prompt,
                    allowed_tools=spec.tools,
                    llm=router.for_role(spec.role),
                    tools=registry,
                    bus=bus,
                    ctx=ctx,
                    max_steps=spec.max_steps,
                    task_id=tid,
                )
                for spec in ROLE_SPECS.values()
            }
            engine = FlowEngine(
                graph=graph,
                agents=agents,
                bus=bus,
                runs_dir=self.store.runs_dir,
                max_rounds=self.settings.workflow.max_rounds,
            )
            state = await engine.run(data, resume=resume)
            self.store.save_result(state)
            return state
        finally:
            sinker.close()
