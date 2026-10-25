"""FastAPI 服务：REST + SSE 流式事件。

端点：
    GET  /                        Web 控制台（web/index.html）
    GET  /api/config              当前 LLM provider 等运行配置
    GET  /api/tasks               历史任务列表（来自任务台账）
    POST /api/tasks               创建任务（fixture 名或完整 input）
    GET  /api/tasks/{id}/result   任务状态 + 最终报告
    GET  /api/tasks/{id}/events   SSE 实时事件流（先回放历史，再续流）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from devlens.config import PROJECT_ROOT, load_settings
from devlens.core.events import Event, EventBus
from devlens.runner import SUPPORTED_SCENARIOS, TaskRunner
from devlens.workflow.engine import FlowState

app = FastAPI(title="DevLens Agent", version="0.1.0")
_settings = load_settings()


class CreateTaskRequest(BaseModel):
    scenario: str
    fixture: str | None = None            # 如 "issues/ISSUE-101.json"
    input: dict[str, Any] = Field(default_factory=dict)


class _LiveTask:
    """服务内的活跃任务：事件历史 + 订阅队列 + 最终状态。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.history: list[Event] = []
        self.subscribers: list[asyncio.Queue[Event]] = []
        self.state: FlowState | None = None
        self.error: str | None = None
        self.done = asyncio.Event()

    def collector(self, event: Event) -> None:
        self.history.append(event)
        for q in list(self.subscribers):
            q.put_nowait(event)


_lives: dict[str, _LiveTask] = {}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "index.html")


@app.get("/api/config")
async def config() -> dict[str, Any]:
    cfg = _settings.llm.openai_compatible if _settings.llm.provider == "openai_compatible" else None
    return {
        "provider": _settings.llm.provider,
        "model_map": cfg.model_map if cfg else {"default": "mock-policy"},
        "scenarios": list(SUPPORTED_SCENARIOS),
    }


@app.get("/api/tasks")
async def list_tasks() -> list[dict[str, Any]]:
    return TaskRunner(_settings).store.list_tasks()


@app.post("/api/tasks")
async def create_task(req: CreateTaskRequest) -> dict[str, str]:
    if req.scenario not in SUPPORTED_SCENARIOS:
        raise HTTPException(400, f"未知场景: {req.scenario}")
    data: dict[str, Any] = dict(req.input)
    if req.fixture:
        path = PROJECT_ROOT / "fixtures" / req.fixture
        if not path.exists():
            raise HTTPException(404, f"fixture 不存在: {req.fixture}")
        data = json.loads(path.read_text(encoding="utf-8"))
    if not data:
        raise HTTPException(400, "缺少任务数据（fixture 或 input 二选一）")

    task_id = str(data.get("id") or f"{req.scenario}-web")
    live = _LiveTask(task_id)
    _lives[task_id] = live
    runner = TaskRunner(_settings)

    async def _run() -> None:
        bus = EventBus()
        bus.subscribe_fn(live.collector)
        try:
            live.state = await runner.run_task(req.scenario, data, task_id=task_id, bus=bus)
        except Exception as exc:  # noqa: BLE001
            live.error = str(exc)
        finally:
            live.done.set()

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.get("/api/tasks/{task_id}/result")
async def task_result(task_id: str) -> dict[str, Any]:
    live = _lives.get(task_id)
    if live:
        state = live.state
        report = TaskRunner(_settings).store.pick_report(state) if state else ""
        return {
            "task_id": task_id,
            "status": state.status if state else ("error" if live.error else "running"),
            "error": live.error,
            "report": report,
            "evidence": state.evidence() if state else [],
        }
    state = TaskRunner(_settings).store.get_state(task_id)
    if not state:
        raise HTTPException(404, f"任务不存在: {task_id}")
    store = TaskRunner(_settings).store
    return {
        "task_id": task_id,
        "status": state.status,
        "error": state.error,
        "report": store.pick_report(state),
        "evidence": state.evidence(),
    }


@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str) -> StreamingResponse:
    live = _lives.get(task_id)
    if not live:
        raise HTTPException(404, f"活跃任务不存在: {task_id}")

    queue: asyncio.Queue[Event] = asyncio.Queue()
    live.subscribers.append(queue)
    seen = {id(e) for e in live.history}

    async def stream():
        for event in list(live.history):
            seen.add(id(event))
            yield f"data: {event.to_json()}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if live.done.is_set() and queue.empty():
                    yield 'data: {"type": "task_closed"}\n\n'
                    break
                continue
            if id(event) in seen:
                continue
            seen.add(id(event))
            yield f"data: {event.to_json()}\n\n"
        live.subscribers.remove(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def serve(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        "devlens.server.app:app",
        host=host or _settings.server.host,
        port=port or _settings.server.port,
        reload=False,
    )


__all__ = ["app", "serve", "Path"]
