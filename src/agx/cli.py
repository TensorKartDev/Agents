"""Command line interface for the AGX framework."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.request

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from .agents.orchestrator import Orchestrator
from .autogen_runner import AutogenOrchestrator
from .config import ProjectConfig
from .remote_worker import discover_worker_agents, execute_remote_task
from .tasks.base import TaskState
from .tasks.runner import TaskRunner
from .workspace import resolve_workspace_paths

app = typer.Typer(help="AGX framework CLI")
console = Console()


def _render_plan(config: ProjectConfig) -> None:
    plan = Table(title="Execution Plan", show_lines=True)
    plan.add_column("Task ID")
    plan.add_column("Agent")
    plan.add_column("Description")
    for spec in config.tasks:
        plan.add_row(spec.id, spec.agent, spec.description)
    console.print(plan)


@app.command()
def run(
    config_path: Path = typer.Argument(..., help="Path to YAML configuration"),
    show_trace: bool = False,
    engine: str = typer.Option("autogen", help="Engine to use: autogen or legacy"),
) -> None:
    """Execute the tasks described in the given config file."""

    config = ProjectConfig.from_file(str(config_path))
    console.print(f"[bold green]Running project[/] {config.name} (engine={engine})")
    _render_plan(config)

    task_specs = list(config.tasks)
    results: dict[str, str] = {}
    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("{task.fields[status]}"),
        transient=False,
    )

    interactive = sys.stdin.isatty()

    def approval_prompt(task) -> bool:
        reason = getattr(task, "reason", "") or "Approval required."
        console.print(f"[yellow]Human approval required[/]: {reason}")
        return typer.confirm("Approve and continue?", default=False)

    approval_callback = approval_prompt if interactive else None

    if engine == "legacy":
        orchestrator = Orchestrator(config)
        orchestrator.runner._approval_callback = approval_callback
    else:
        orchestrator = AutogenOrchestrator(config, approval_callback=approval_callback)

    with progress:
        progress_tasks = {
            task_spec.id: progress.add_task(
                f"{task_spec.id} - {task_spec.description}", status="[yellow]pending", start=False
            )
            for task_spec in task_specs
        }
        if engine == "legacy":
            for task_spec in task_specs:
                progress.update(progress_tasks[task_spec.id], status="[cyan]queued", start=True)
            run_results = orchestrator.runner.run_all(orchestrator.tasks)
            for task_id, result in run_results.items():
                results[task_id] = result.output
                status = "[green]completed ✅" if result.state == TaskState.COMPLETED else "[red]failed"
                if result.state == TaskState.WAITING_HUMAN:
                    status = "[yellow]waiting for approval"
                progress.update(progress_tasks[task_id], status=status)
        else:
            for task_spec in task_specs:
                progress.update(progress_tasks[task_spec.id], status="[cyan]queued", start=True)
            results = orchestrator.run()
            for task_id, output in results.items():
                status = "[green]completed ✅"
                if isinstance(output, str) and output.startswith("WAITING_HUMAN"):
                    status = "[yellow]waiting for approval"
                progress.update(progress_tasks[task_id], status=status)

    table = Table(title="Task outputs", show_lines=True)
    table.add_column("Task ID")
    table.add_column("Output")
    for task_id, output in results.items():
        table.add_row(task_id, output)
    console.print(table)

    if show_trace and engine == "legacy":
        for task in orchestrator.tasks:
            result = orchestrator.runner.results().get(task.id)
            if not result:
                continue
            console.rule(f"Trace for {task.id}")
            for entry in result.trace:
                console.print(entry)
    elif show_trace:
        console.print("[yellow]Trace output is only available for the legacy engine.[/]")


@app.command()
def inspect(config_path: Path = typer.Argument(..., help="Config to inspect")) -> None:
    """Print the agents, tasks, and tools defined by a configuration file."""

    config = ProjectConfig.from_file(str(config_path))
    orchestrator = Orchestrator(config)
    console.print(f"[bold]Project:[/] {config.name}\n{config.description or ''}")
    console.print("[bold]Agents[/]")
    for agent in orchestrator.agents.values():
        console.print(f"- {agent.name}: tools={[name for name in agent.tools.keys()]}")
    console.print("[bold]Tasks[/]")
    for task in orchestrator.tasks:
        console.print(f"- {task.id} -> {task.agent_name}: {task.description}")

@app.command()
def worker(
    runtime_url: str = typer.Option(..., help="AGX runtime base URL, for example http://host-a:8000"),
    username: str = typer.Option(..., help="AGX username or email"),
    password: str = typer.Option(..., help="AGX password", prompt=True, hide_input=True),
    worker_id: str = typer.Option("", help="Stable worker id; defaults to username-hostname"),
    poll_interval: float = typer.Option(3.0, help="Seconds between polls when idle"),
    agents_dir: Path | None = typer.Option(None, help="Override AGX_AGENTS_DIR for worker discovery"),
    registry_path: Path | None = typer.Option(None, help="Override AGX_AGENT_REGISTRY for worker discovery"),
) -> None:
    """Run this CLI as a remote AGX worker."""

    base_url = runtime_url.rstrip("/")
    workspace = resolve_workspace_paths(Path.cwd())
    resolved_agents_dir = (agents_dir or workspace.agents_dir).expanduser().resolve()
    resolved_registry_path = (registry_path or workspace.registry_path).expanduser().resolve()
    chosen_worker_id = worker_id.strip() or f"{username.split('@', 1)[0]}-{socket.gethostname()}"
    chosen_worker_id = chosen_worker_id.replace(" ", "-").lower()

    token = _worker_login(base_url, username=username, password=password)
    console.print(f"[bold green]Worker online[/] {chosen_worker_id} -> {base_url}")
    console.print(f"[dim]Discovering agents from {resolved_agents_dir} using {resolved_registry_path}[/]")

    while True:
        agents = discover_worker_agents(
            agents_dir=resolved_agents_dir,
            registry_path=resolved_registry_path,
            base_dir=Path.cwd(),
        )
        _worker_register(
            base_url,
            token=token,
            payload={
                "worker_id": chosen_worker_id,
                "hostname": socket.gethostname(),
                "runtime_url": base_url,
                "capabilities": {
                    "local_tools": True,
                    "agent_execution": True,
                    "agents_dir": str(resolved_agents_dir),
                },
                "agents": agents,
            },
        )
        assignment = _worker_poll(base_url, token=token, worker_id=chosen_worker_id)
        if not assignment:
            time.sleep(max(0.5, poll_interval))
            continue

        lease_id = str(assignment.get("lease_id") or "")
        task_id = str(assignment.get("task_id") or "")
        run_id = str(assignment.get("run_id") or "")
        agent_slug = str(assignment.get("agent_slug") or "")
        local_agent = next((item for item in agents if item.get("agent_slug") == agent_slug), None)
        if local_agent is None:
            _worker_complete(
                base_url,
                token=token,
                lease_id=lease_id,
                payload={
                    "worker_id": chosen_worker_id,
                    "error": f"Agent '{agent_slug}' is not available on this worker",
                    "console": [],
                },
            )
            continue

        console.print(f"[cyan]Running[/] {task_id} from {agent_slug} for run {run_id}")
        try:
            result = execute_remote_task(
                config_path=Path(str(local_agent.get("config_path"))),
                task_id=task_id,
                engine=str(assignment.get("engine") or "autogen"),
                input_value=assignment.get("input"),
                context_value=dict(assignment.get("context") or {}),
                run_id=run_id,
            )
            payload = {"worker_id": chosen_worker_id, **result}
        except Exception as exc:
            payload = {
                "worker_id": chosen_worker_id,
                "error": str(exc),
                "console": [],
            }
        _worker_complete(base_url, token=token, lease_id=lease_id, payload=payload)


def _worker_login(base_url: str, *, username: str, password: str) -> str:
    data = _http_json(
        f"{base_url}/api/worker/login",
        payload={"username": username, "password": password},
    )
    token = str(data.get("session_token") or "")
    if not token:
        raise RuntimeError("Worker login did not return a session token")
    return token


def _worker_register(base_url: str, *, token: str, payload: dict) -> dict:
    return _http_json(
        f"{base_url}/api/worker/register",
        payload=payload,
        bearer_token=token,
    )


def _worker_poll(base_url: str, *, token: str, worker_id: str) -> dict | None:
    data = _http_json(
        f"{base_url}/api/worker/poll",
        payload={"worker_id": worker_id},
        bearer_token=token,
    )
    assignment = data.get("assignment")
    return assignment if isinstance(assignment, dict) else None


def _worker_complete(base_url: str, *, token: str, lease_id: str, payload: dict) -> dict:
    return _http_json(
        f"{base_url}/api/worker/tasks/{lease_id}/complete",
        payload=payload,
        bearer_token=token,
    )


def _http_json(url: str, *, payload: dict, bearer_token: str = "") -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"{url} -> HTTP {exc.code}: {detail}") from exc


if __name__ == "__main__":  # pragma: no cover
    app()
