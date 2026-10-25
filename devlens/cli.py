"""命令行入口（argparse + rich，零额外依赖）。

用法：
    python -m devlens.cli run issue --input fixtures/issues/ISSUE-101.json
    python -m devlens.cli run pr    --input fixtures/prs/PR-42.json
    python -m devlens.cli run ci    --input fixtures/ci/JOB-88.json
    python -m devlens.cli eval
    python -m devlens.cli serve --port 8000
    python -m devlens.cli tasks
    python -m devlens.cli replay <task_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from devlens.core.events import Event, EventBus, EventType
from devlens.eval.harness import render_text, run_eval
from devlens.runner import SUPPORTED_SCENARIOS, TaskRunner
from devlens.server.app import serve

console = Console()

_STYLES: dict[EventType, tuple[str, str]] = {
    EventType.TASK_STARTED: ("bold cyan", "▶"),
    EventType.NODE_ENTERED: ("bold blue", "◆"),
    EventType.NODE_EXITED: ("blue", "✔"),
    EventType.AGENT_THOUGHT: ("green", "💭"),
    EventType.TOOL_CALLED: ("yellow", "🔧"),
    EventType.TOOL_RESULT: ("dim", "  ↳"),
    EventType.REFLECTION: ("bold magenta", "↺"),
    EventType.CHECKPOINT_SAVED: ("dim", "💾"),
    EventType.TASK_FINISHED: ("bold green", "■"),
    EventType.TASK_FAILED: ("bold red", "✗"),
}


def _print_event(event: Event, verbose: bool = False) -> None:
    style, icon = _STYLES.get(event.type, ("white", "·"))
    text = event.message if event.type != EventType.TOOL_RESULT or verbose else event.message[:200]
    node = f"[{event.node}]" if event.node else ""
    console.print(f"{icon} {style}[{event.type.value}]{node} {text}", style=style)


def cmd_run(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    input_path = Path(args.input)
    if not input_path.exists():
        console.print(f"[red]输入文件不存在: {input_path}[/red]")
        return 2
    data = json.loads(input_path.read_text(encoding="utf-8"))

    runner = TaskRunner()
    bus = EventBus()
    bus.subscribe_fn(lambda e: _print_event(e, verbose=args.verbose))

    console.rule(f"[bold]DevLens Agent · {args.scenario} · {data.get('id', '')}")
    state = asyncio.run(runner.run_task(args.scenario, data, resume=args.resume, bus=bus))
    report = runner.store.pick_report(state)

    console.print()
    console.print(
        Panel(
            Markdown(report or "（无产出）"),
            title=f"最终报告 · {data.get('id', '')}",
            subtitle=f"状态: {state.status} | 轮次: {state.rounds} | 证据: {len(state.evidence())} 条",
        )
    )
    verdict = state.blackboard.get("last_verdict", {})
    if verdict:
        console.print(
            f"[bold]Critic 裁决:[/bold] {verdict.get('verdict')} "
            f"(score={verdict.get('score')}) — {verdict.get('feedback', '')}"
        )
    result_path = runner.store.task_dir(str(data.get("id", ""))) / "result.md"
    console.print(f"[dim]报告已保存: {result_path}[/dim]")
    return 0 if state.status == "finished" else 1


def cmd_eval(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(run_eval())
    console.print(render_text(report))
    console.print(f"\n[dim]报告已保存: eval_report.json[/dim]")
    return 0 if report["total_score"] >= 80 else 1


def cmd_serve(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    console.print(f"[bold green]DevLens Agent Web 控制台[/bold green] http://{args.host}:{args.port}")
    serve(args.host, args.port)
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    store = TaskRunner().store
    tasks = store.list_tasks()
    if not tasks:
        console.print("（暂无任务记录）")
        return 0
    table = Table(title="任务台账")
    for col in ("task_id", "scenario", "status", "rounds", "evidence", "verdict"):
        table.add_column(col)
    for t in tasks:
        table.add_row(*(str(t.get(c, "")) for c in ("task_id", "scenario", "status", "rounds", "evidence_count", "verdict")))
    console.print(table)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    events = TaskRunner().store.read_events(args.task_id)
    if not events:
        console.print(f"[red]无事件记录: {args.task_id}[/red]")
        return 2
    for raw in events:
        _print_event(Event.model_validate(raw), verbose=args.verbose)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devlens", description="DevLens Agent —— 研发协作多智能体框架")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="运行一个研发场景任务")
    run_p.add_argument("scenario", choices=SUPPORTED_SCENARIOS, help="场景: issue|pr|ci")
    run_p.add_argument("--input", required=True, help="任务输入 JSON 路径")
    run_p.add_argument("--resume", action="store_true", help="从检查点续跑")
    run_p.add_argument("-v", "--verbose", action="store_true", help="输出完整工具结果")
    run_p.set_defaults(func=cmd_run)

    eval_p = sub.add_parser("eval", help="离线评测（三场景固定案例）")
    eval_p.set_defaults(func=cmd_eval)

    serve_p = sub.add_parser("serve", help="启动 Web 控制台")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.set_defaults(func=cmd_serve)

    tasks_p = sub.add_parser("tasks", help="查看任务台账")
    tasks_p.set_defaults(func=cmd_tasks)

    replay_p = sub.add_parser("replay", help="回放任务事件流水")
    replay_p.add_argument("task_id")
    replay_p.add_argument("-v", "--verbose", action="store_true")
    replay_p.set_defaults(func=cmd_replay)

    return parser


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
