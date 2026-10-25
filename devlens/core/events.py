"""事件总线：整个框架的可观测性基座。

设计参考：
    * OpenHands 的事件溯源（event stream）：一次任务运行 = 一串类型化事件；
    * Claude Code 的 hook 事件：外部系统（CLI 渲染 / SSE 推送 / 评测采集）
      都通过订阅事件获得进度，而不侵入 Agent 内核。

事件同时落盘为 .runs/<task_id>/events.jsonl，支持离线回放（devlens replay）。
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field


class EventType(str, Enum):
    TASK_STARTED = "task_started"
    NODE_ENTERED = "node_entered"
    NODE_EXITED = "node_exited"
    AGENT_THOUGHT = "agent_thought"      # Agent 的自然语言输出（思考/结论）
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    REFLECTION = "reflection"            # Critic 打回重做
    CHECKPOINT_SAVED = "checkpoint_saved"
    TASK_FINISHED = "task_finished"
    TASK_FAILED = "task_failed"


class Event(BaseModel):
    type: EventType
    task_id: str
    node: str | None = None
    agent: str | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False)


Subscriber = Callable[[Event], None]


class EventBus:
    """进程内事件总线。

    * subscribe_queue(): 返回 asyncio.Queue，供 SSE / 异步消费者使用；
    * subscribe_fn():    注册同步回调，供 CLI 渲染 / 测试断言使用；
    * emit():            由引擎在异步上下文中调用，永不抛错（可观测性不能拖垮主流程）。
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[Event]] = []
        self._fns: list[Subscriber] = []
        self._transcript: list[Event] = []

    # ---- 订阅 ----
    def subscribe_queue(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe_queue(self, q: asyncio.Queue[Event]) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def subscribe_fn(self, fn: Subscriber) -> None:
        self._fns.append(fn)

    # ---- 发布 ----
    def emit(self, event: Event) -> None:
        self._transcript.append(event)
        for fn in list(self._fns):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 观察者异常不影响主流程
                pass
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except Exception:  # noqa: BLE001
                pass

    def history(self) -> list[Event]:
        return list(self._transcript)


class EventSinker:
    """把事件持久化到 .runs/<task_id>/events.jsonl，支持离线回放。"""

    def __init__(self, task_dir: Path) -> None:
        task_dir.mkdir(parents=True, exist_ok=True)
        self._path = task_dir / "events.jsonl"
        self._fh = self._path.open("a", encoding="utf-8")

    def __call__(self, event: Event) -> None:
        self._fh.write(event.to_json() + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
